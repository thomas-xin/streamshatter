import asyncio
import base64
import hashlib
import json
from math import isfinite
import os
import random
import time
from urllib.parse import quote_plus
import niquests

chunk_size = 262144
base_chunk = 16384
COLOURS = ["\x1b[38;5;16m█"]
COLOURS.extend(f"\x1b[38;5;{i}m█" for i in range(232, 256))
COLOURS.append("\x1b[38;5;15m█")

session = None
def generate_session(multiplexed=True):
	globals()["session"] = niquests.AsyncSession(multiplexed=multiplexed)
	return session
generate_session()
def is_url(url):
	return "://" in url and url.split("://", 1)[0].rstrip("s") in ("http", "hxxp", "ftp", "fxp")
def shash(s): return base64.urlsafe_b64encode(hashlib.sha256(s if type(s) is bytes else str(s).encode("utf-8")).digest()).rstrip(b"==").decode("ascii")
def uhash(s): return min([shash(s), quote_plus(s.removeprefix("https://"))], key=len)
def header():
	return {
		"Accept": "*/*",
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0 AppleWebKit/537.36 Chrome/134.0.0.0 Safari/537.36 Edg/134.0.3124.85",
		"DNT": "1",
		"X-Forwarded-For": ".".join(str(random.randint(0, 255)) for _ in range(4)),
	}
def nth_file(tag, chunk=0):
	return base64.urlsafe_b64encode(chunk.to_bytes(chunk.bit_length() + 7 >> 3, "big")).rstrip(b"==").decode("ascii") + "~" + tag
def box(i):
	if i < 0:
		return "\x1b[38;5;196m█"
	return COLOURS[round(min(1, i) * (len(COLOURS) - 1))]
def time_disp(s, rounded=True):
	if not isfinite(s):
		return str(s)
	if rounded:
		s = round(s)
	output = str(s % 60)
	if len(output) < 2:
		output = "0" + output
	if s >= 60:
		temp = str((s // 60) % 60)
		if len(temp) < 2 and s >= 3600:
			temp = "0" + temp
		output = temp + ":" + output
		if s >= 3600:
			temp = str((s // 3600) % 24)
			if len(temp) < 2 and s >= 86400:
				temp = "0" + temp
			output = temp + ":" + output
			if s >= 86400:
				output = str(s // 86400) + ":" + output
	else:
		output = "0:" + output
	return output
def calc_bps(bps):
	for suffix in ("bps", "kbps", "Mbps", "Gbps", "Tbps", "Pbps", "Ebps", "Zbps", "Ybps"):
		bps = round(bps, 4)
		if bps < 1000:
			if bps.is_integer():
				bps = int(bps)
			return f"{bps} {suffix}"
		bps /= 1000
	return "ERR"
def sample(arr, n):
	while len(arr) > n * 2:
		arr = [(a + b) / 2 for a, b in zip(arr[::2], arr[1::2])]
	if len(arr) > n:
		indices = [x * len(arr) / n for x in range(n)]
		arr.append(arr[-1])
		return [arr[int(i)] * (1 - i % 1) + arr[int(i) + 1] * (i % 1) for i in indices]
	return arr
def update_progress(ctx, force=False, use_original_timestamp=False):
	ct = time.perf_counter()
	if not force and (ct - ctx["last"] < 0.03 or not ctx.get("log_progress", True)):
		return
	ctx["last"] = ct
	maxbar = 64
	samples = [chunk[-1] / chunk[1] for chunk in ctx["chunkinfo"]]
	s = "".join(map(box, sample(samples, maxbar)))
	dt = max(0.001, ct - ctx["start"])
	timer = time_disp(dt)
	progress = sum(chunk[-1] for chunk in ctx["chunkinfo"])
	percentage = round(progress / ctx["size"] * 100, 4)
	if percentage.is_integer():
		percentage = int(percentage)
	if use_original_timestamp:
		bps = ctx["size"] * 8 / dt
	else:
		i = 0
		for i, d in enumerate(ctx["deltas"]):
			if ct < d[0] + 5:
				break
		ctx["deltas"] = ctx["deltas"][i:]
		bps = sum(d[1] for d in ctx["deltas"]) * 8 / min(5, dt)
	bpst = calc_bps(bps)
	completed = sum(chunk[-1] == chunk[1] for chunk in ctx["chunkinfo"])
	chunk_count = len(ctx["chunkinfo"])
	s2 = f" {timer} {completed}/{chunk_count} ({percentage}%, {bpst})"
	chars = min(maxbar, chunk_count) + len(s2)
	s += "\x1b[38;5;7m" + s2
	s += " " * (120 - chars)
	print(s, end="\r")
	if ctx["forkable"] and chunk_count < ctx["limit"]:
		mcc = ctx.get("max_chunk_count", 0)
		ccc = chunk_count - completed
		if ct - ctx["last_split"] > 1 and (bps > ctx["last_bps"] * 1.05 or ccc < mcc):
			# Allow no more than 4 stalled/errored requests at a time
			if sum(chunk[-1] <= 0 for chunk in ctx["chunkinfo"]) < 4:
				ctx["max_chunk_count"] = max(mcc, ccc + 1)
				return True
		elif ct - ctx["last_split"] > 5:
			ctx["last_bps"] = bps
			ctx["last_split"] = time.perf_counter()

async def write_request(ctx, chunk, resp, url, method, headers, data, filename):
	start = chunk[0]
	file = os.path.join(ctx["cache_folder"], nth_file(uhash(url), start))
	ctx["chunkinfo"].append(chunk)
	attempts = 0
	if not session.multiplexed and ctx["multiplexed"]:
		generate_session()
	with open(file, "wb+") as f:
		while True:
			timeout = (attempts + 1) * 5
			try:
				if not resp:
					resp = await asyncio.wait_for(session.request(method, url, headers=headers, data=data, stream=True, timeout=timeout, verify=ctx["verify"]), timeout=timeout + 1)
				it = await asyncio.wait_for(resp.iter_content(base_chunk), timeout=timeout)
				resp.raise_for_status()
				if "Range" in headers:
					assert resp.headers["content-range"].split("/", 1)[0].split(None, 1)[-1].split("-", 1)[0] == headers["Range"].split("=", 1)[-1].split("-", 1)[0], "Server failed to serve range header as specified!"
				size = chunk[1]
				try:
					while True:
						fut = it.__anext__()
						try:
							content = await asyncio.wait_for(fut, timeout=timeout)
						except AttributeError:
							raise TimeoutError
						f.write(content)
						chunk[-1] = min(max(len(content), chunk[-1] + len(content)), size) if size >= 0 else chunk[-1] + len(content)
						ctx["deltas"].append((time.perf_counter(), len(content)))
						split = update_progress(ctx)
						if chunk[-1] == size:
							break
						if split and chunk[-1] + chunk_size < size and size - chunk[-1] >= max(chunk[1] - chunk[-1] for chunk in ctx["chunkinfo"]) / 2:
							ct = time.perf_counter()
							dt = max(0.001, ct - ctx["start"])
							ctx["last_bps"] = sum(d[1] for d in ctx["deltas"]) * 8 / min(5, dt)
							ctx["last_split"] = time.perf_counter()
							offset = chunk[-1] + round((size - chunk[-1]) / (2 if start else 64))
							chunk2 = [start + offset, size - offset, 0]
							rheaders = headers.copy()
							rheaders["Priority"] = "i"
							rheaders["Range"] = f"bytes={start + offset}-{start + size - 1}" if ctx["allow_range_ends"] else f"bytes={start + offset}-"
							fut = asyncio.create_task(write_request(ctx, chunk2, None, resp.url, method, rheaders, data, filename))
							fut.start = chunk2[0]
							ctx["workers"].append(fut)
							size = chunk[1] = offset
				except (StopIteration, StopAsyncIteration):
					pass
				f.flush()
				if size > 0:
					f.truncate(size)
				f.seek(0, os.SEEK_END)
				if size > 0:
					assert f.tell() == size, (f.tell(), size)
			except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError, niquests.ConnectionError, niquests.ConnectTimeout, niquests.ReadTimeout, niquests.Timeout, niquests.exceptions.ChunkedEncodingError):
				size = chunk[1]
				offset = chunk[-1]
				chunk[-1] = -0.01
				# If a simple error occurs (e.g. timeout) but some data was already received, create a new request and end the current one
				if offset > 0:
					generate_session(ctx["multiplexed"])
					if offset < size:
						chunk2 = [start + offset, size - offset, 0]
						rheaders = headers.copy()
						rheaders["Priority"] = "i"
						rheaders["Range"] = f"bytes={start + offset}-{start + size - 1}" if ctx["allow_range_ends"] else f"bytes={start + offset}-"
						fut = asyncio.create_task(write_request(ctx, chunk2, None, url, method, rheaders, data, filename))
						fut.start = chunk2[0]
						ctx["workers"].append(fut)
						size = chunk[1] = offset
					chunk[-1] = size
					break
			except Exception as ex:
				chunk[-1] = -0.01
				print(repr(ex))
				if ctx["debug"]:
					print(resp.headers)
					raise
			else:
				chunk[-1] = size
				break
			finally:
				if resp is not None:
					try:
						await asyncio.wait_for(resp.close(), timeout=timeout)
					except Exception:
						pass
			resp = None
			f.seek(0)
			f.truncate(0)
			chunk[-1] = -0.01
			update_progress(ctx, force=True)
			await asyncio.sleep((attempts + random.random()) ** 2 + 1)
			attempts += 1
			generate_session(ctx["multiplexed"])
	assert os.path.exists(file), f"Chunk `{file}` missing!"
	return file

async def shatter_request(url, method="get", headers={}, data=None, filename=None, fileobj=None, cache_folder="", limit=1024, verify=True, debug=False, log_progress=True):
	t = time.perf_counter()
	head = header()
	head.update(headers)
	multiplexed = True
	try:
		resp = await asyncio.wait_for(session.request(method, url, headers=head, data=data, stream=True, verify=verify, timeout=3), timeout=4)
	except (asyncio.TimeoutError, niquests.exceptions.ConnectTimeout, niquests.exceptions.MultiplexingError):
		generate_session(False)
		resp = await session.request(method, url, headers=head, data=data, stream=True, verify=verify, timeout=None)
		multiplexed = False
	await resp.iter_content(base_chunk * 4)
	resp.raise_for_status()
	if not filename:
		filename = resp.headers.get("attachment-filename") or resp.headers.get("content-disposition", "").split("filename=", 1)[-1].strip().strip('"').strip("'") or url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
		import re
		filename = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", filename)
		if "." not in filename:
			ctype = resp.headers.get("content-type")
			if ctype and ctype != "application/octet-stream":
				import mimetypes
				ext = mimetypes.guess_extension(ctype)
				if ext:
					filename = filename + "." + ext
	try:
		size = int(resp.headers.get("content-length") or resp.headers["content-range"].rsplit("/", 1)[-1])
	except (KeyError, ValueError):
		size = -1
	chunk = [0, size, 0]
	single = limit <= 1 or size <= 0 or "bytes" not in resp.headers.get("accept-ranges", "").casefold()
	ctx = dict(
		log_progress=log_progress,
		debug=debug,
		url=url,
		start=t,
		last=0,
		size=size,
		last_bps=0,
		last_split=time.perf_counter() - 0.5,
		cache_folder=cache_folder,
		limit=limit,
		verify=verify,
		forkable=not single,
		multiplexed=multiplexed,
		allow_range_ends=True,
		deltas=[],
		chunkinfo=[],
		workers=[],
	)
	fut = asyncio.create_task(write_request(ctx, chunk, resp, url, method, head, data, filename))
	fut.start = 0
	ctx["workers"].append(fut)
	fn = filename + "~"
	import shutil
	with open(fn, "ab+") as f:
		f.seek(0)
		while ctx["workers"]:
			# Important invariant: Newly bisected workers will always be after the original ones in the list
			ctx["workers"].sort(key=lambda fut: fut.start)
			file = await ctx["workers"].pop(0)
			with open(file, "rb") as g:
				shutil.copyfileobj(g, f)
			try:
				os.remove(file)
			except Exception:
				pass
		if size > 0:
			f.truncate(size)
	assert os.path.exists(fn) and (size < 0 or os.path.getsize(fn) == size), f"Expected {size} bytes, received {os.path.getsize(fn)}"
	try:
		os.replace(fn, filename)
	except PermissionError:
		with open(fn, "rb") as f:
			if not fileobj:
				fileobj = open(filename, "wb")
			fileobj.seek(0)
			shutil.copyfileobj(f, fileobj)
		try:
			os.remove(fn)
		except PermissionError:
			pass
	update_progress(ctx, force=True, use_original_timestamp=True)
parallel_request = shatter_request

async def resolve_file(obj):
	import pathlib
	if isinstance(obj, pathlib.Path) or isinstance(obj, str) and not is_url(obj):
		return open(obj, "rb+")
	if isinstance(obj, str):
		import tempfile
		file = tempfile.NamedTemporaryFile()
		await shatter_request(obj, filename=file.name, fileobj=file, log_progress=False)
		file.seek(0)
		return file
	import tempfile
	file = tempfile.NamedTemporaryFile()
	file.write(obj)
	file.seek(0)
	return file

try:
	from importlib.metadata import version
	__version__ = version("streamshatter")
except Exception:
	__version__ = "0.0.0-unknown"

def main():
	import argparse
	parser = argparse.ArgumentParser(
		prog="streamshatter",
		description="Multiplexed chunked file downloader",
	)
	parser.add_argument("-V", '--version', action='version', version=f'%(prog)s {__version__}')
	parser.add_argument("-H", '--headers', help="HTTP headers, interpreted as JSON", required=False, default="{}")
	parser.add_argument("-c", '--cache-folder', help="Folder to store temporary files", required=False, default=os.path.join(__file__.replace("\\", "/").rsplit("/", 1)[0], "cache"))
	parser.add_argument("-l", '--limit', help="Limits the amount of chunks to download; defaults to 1024", type=int, required=False, default=1024)
	parser.add_argument("-s", "--ssl", action=argparse.BooleanOptionalAction, default=True, help="Enforces SSL verification; defaults to TRUE")
	parser.add_argument("-d", "--debug", action=argparse.BooleanOptionalAction, default=False, help="Terminates immediately upon non-timeout errors, and writes the response data for errored chunks; defaults to FALSE")
	parser.add_argument("-lp", "--log-progress", action=argparse.BooleanOptionalAction, default=True, help="Continually updates a progress bar in the standard output; defaults to TRUE")
	parser.add_argument("url", help="Target URL")
	parser.add_argument("filename", help="Output filename", nargs="?", default="")
	args = parser.parse_args()
	if not os.path.exists(args.cache_folder):
		os.mkdir(args.cache_folder)
	if os.name == "nt":
		os.system("color")
	asyncio.run(shatter_request(
		url=args.url,
		filename=args.filename,
		headers=json.loads(args.headers),
		cache_folder=args.cache_folder,
		limit=args.limit,
		verify=args.ssl,
		debug=args.debug,
		log_progress=args.log_progress,
	))

if __name__ == "__main__":
	main()