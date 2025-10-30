import asyncio
import concurrent.futures
import itertools
import json
from math import isfinite, inf
import os
import random
import shutil
import sys
import tempfile
import time
import niquests
import urllib3

MIN_SPLIT = 262144
CHUNK_SIZE = 16384
COLOURS = ["\x1b[38;5;16m█"]
COLOURS.extend(f"\x1b[38;5;{i}m█" for i in range(232, 256))
COLOURS.append("\x1b[38;5;15m█")

def generate_session(multiplexed=True):
	return niquests.AsyncSession(
		multiplexed=multiplexed,
		pool_connections=16,
		pool_maxsize=16,
		happy_eyeballs=True,
	)

def header():
	return {
		"Accept": "*/*",
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0 AppleWebKit/537.36 Chrome/134.0.0.0 Safari/537.36 Edg/134.0.3124.85",
		"DNT": "1",
		"X-Forwarded-For": "34." + ".".join(str(random.randint(1, 254)) for _ in range(3)),
	}
def box(i):
	if i < 0:
		return "\x1b[38;5;196m█"
	elif i > 1:
		return "\x1b[38;5;46m█"
	return COLOURS[round(i * (len(COLOURS) - 1))]
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


class ChunkManager:

	def __init__(self, url, method="get", headers={}, data=None, filename=None, fileobj=None, concurrent_limit=64, size_limit=1099511627776, verify=None, debug=False, log_progress=True, timeout=None):
		self.url = url
		self.method = method
		self.headers = header()
		self.headers.update(headers)
		self.data = data or None
		self.filename = filename
		self.fileobj = fileobj
		self.concurrent_limit = concurrent_limit
		self.size_limit = size_limit
		self.verify = verify
		self.debug = debug
		self.log_progress = log_progress
		self.timeout = timeout
		self.multiplexed = self.concurrent_limit > 1
		self.allow_range_ends = True
		self.timestamp = 0
		self.last_update = 0
		self.latency = inf
		self.workers = []
		self.sessions = set()
		self.request_count = 0
		self.max_bps = 0
		self.max_single_bps = 0

	async def probe_request(self):
		self.session = session = generate_session(self.multiplexed)
		self.sessions.add(session)
		verify = self.verify if self.verify is not None else True
		t = time.perf_counter()
		req = session.request(
			self.method,
			self.url,
			headers=self.headers,
			data=self.data,
			stream=True,
			verify=verify,
			timeout=min(3, self.timeout or 3),
		)
		try:
			self.request_count += 1
			resp = await asyncio.wait_for(req, timeout=4)
			ait = resp.iter_content(CHUNK_SIZE)
			if self.timeout:
				ait = asyncio.wait_for(ait, timeout=self.timeout)
			it = await ait
		except (asyncio.TimeoutError, niquests.exceptions.ConnectTimeout, niquests.exceptions.MultiplexingError):
			self.multiplexed = False
			self.session = session = generate_session(self.multiplexed)
			self.sessions.add(session)
			verify = self.verify or False
			req = session.request(
				self.method,
				self.url,
				headers=self.headers,
				data=self.data,
				stream=True,
				verify=verify,
				timeout=self.timeout,
			)
			if self.timeout:
				req = asyncio.wait_for(req, timeout=self.timeout + 1)
			self.request_count += 1
			resp = await req
			ait = resp.iter_content(CHUNK_SIZE)
			if self.timeout:
				ait = asyncio.wait_for(ait, timeout=self.timeout)
			it = await ait
		self.verify = verify
		resp.raise_for_status()
		self.latency = time.perf_counter() - t
		return resp, it

	def update_progress(self, force=False):
		if not self.log_progress:
			return
		ct = time.perf_counter()
		if not force and ct - self.last_update < 0.03:
			return
		self.last_update = ct
		maxbar = 64
		samples = [box(n) for n in sample([inf if worker.done else worker.pos / worker.size for worker in self.workers], maxbar)]
		for i, worker in enumerate(self.workers):
			if worker.error:
				si = int(i / len(self.workers) * len(samples))
				samples[si] = ("\x1b[38;5;226m" if worker.pos > 0 else "\x1b[38;5;196m") + "█"
		s = "\033[K" + "".join(samples)
		dt = max(0.001, ct - self.timestamp)
		timer = time_disp(dt)
		progress = self.progress / self.size
		percentage = round(progress * 100, 4)
		if percentage.is_integer():
			percentage = int(percentage)
		bpst = calc_bps(self.bps)
		completed = sum(not worker.is_running() for worker in self.workers)
		chunk_count = len(self.workers)
		s2 = f" {timer} {completed}/{chunk_count} ({percentage}%, {bpst})"
		s += "\x1b[38;5;7m" + s2
		print(s, end="\r")
		return progress

	async def join(self):
		fut = asyncio.create_task(self.shatter())
		is_stream = self.fileobj is not None
		if is_stream:
			fp = self.fileobj
		else:
			disk = os.path.splitdrive(self.filename)[0]
			folder = disk + "/.temp"
			os.makedirs(folder, exist_ok=True)
			fp = tempfile.NamedTemporaryFile(dir=folder, delete=False)
		with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
			try:
				try:
					assert fp.seekable() and os.path.exists(fp.name)
				except (AssertionError, AttributeError):
					pass
				else:
					fp.seek(0)
				index = 0
				while index < len(self.workers):
					self.workers.sort(key=lambda worker: worker.range.start)
					worker = self.workers[index]
					worker.is_target = True
					stream = index == 0
					fc = worker.do(stream=stream)
					if stream:
						async for b in fc:
							await asyncio.get_event_loop().run_in_executor(executor, fp.write, b)
					else:
						fc = await fc
						with fc:
							await asyncio.get_event_loop().run_in_executor(executor, shutil.copyfileobj, fc, fp)
					worker.done = True
					index += 1
				if not is_stream:
					fp.close()
					try:
						os.replace(fp.name, self.filename)
					except (PermissionError, OSError):
						shutil.copyfile(fp.name, self.filename)
					asize = os.path.exists(self.filename) and os.path.getsize(self.filename)
					assert self.size <= 0 or asize == self.size, f"Expected {self.size} bytes, received {asize}"
			finally:
				fut.cancel()
				if not is_stream and os.path.exists(fp.name):
					fp.close()
					os.remove(fp.name)
				for session in self.sessions:
					await session.close()
				self.update_progress(force=True)
				print()
		return fp if is_stream else open(self.filename, "rb")

	async def shatter(self):
		if not self.concurrent_limit:
			return
		worker = self.workers[0]
		while self.workers:
			await asyncio.sleep(0.25)
			remaining = [worker for worker in self.workers if worker.is_running()]
			if not remaining:
				break
			if len(remaining) >= self.concurrent_limit:
				continue
			bps_ratio = self.bps / self.max_bps
			multiplier = ((self.size - self.progress) / self.size)
			try:
				multiplier /= bps_ratio
			except ZeroDivisionError:
				pass
			remaining.sort(key=lambda worker: worker.split_priority(multiplier))
			worker = remaining[-1]
			if worker.split_priority(multiplier) <= 0:
				if bps_ratio < 0.125 and worker.bps / self.max_single_bps < 0.5 and time.perf_counter() - worker.timestamp > worker.restart_cooldown:
					worker.needs_restart = True
					worker.restart_cooldown *= 2
				continue
			avg_bps = sum(worker.bps for worker in remaining) / len(remaining)
			ratio = max(1 / 64, min(1 / 2, worker.bps / (avg_bps + worker.bps)))
			worker.split(ratio)

	async def start(self):
		self.timestamp = time.perf_counter()
		resp, it = await self.probe_request()
		if not self.filename or self.filename.replace("\\", "/").endswith("/"):
			if self.filename:
				path = os.path.abspath(self.filename)
				os.makedirs(path, exist_ok=True)
			else:
				path = os.path.abspath(os.curdir)
			filename = (
				resp.headers.get("attachment-filename")
				or resp.headers.get("content-disposition", "").split("filename=", 1)[-1].lstrip('"').split('"', 1)[0].strip().strip('"').strip("'")
				or self.url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
			)
			import re
			filename = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", filename)
			if "." not in filename:
				ctype = resp.headers.get("content-type")
				if ctype and ctype != "application/octet-stream":
					import mimetypes
					ext = mimetypes.guess_extension(ctype)
					if ext:
						filename = filename + "." + ext
			self.filename = os.path.join(path, filename)
		elif not self.fileobj and self.filename == "-":
			self.fileobj = sys.__stdout__.buffer
			sys.stdout = sys.stderr
		try:
			self.size = int(resp.headers.get("content-length") or resp.headers["content-range"].rsplit("/", 1)[-1])
		except (KeyError, ValueError):
			self.size = 0
		if self.size <= 0 or "bytes" not in resp.headers.get("accept-ranges", "").lower():
			self.concurrent_limit = 0
		worker = ChunkWorker(
			ctx=self,
			resp=resp,
			it=it,
			url=resp.url,
		)
		self.workers.append(worker)
		return await self.join()

	def run(self):
		return asyncio.run(self.start())

	@property
	def done(self):
		return not self.workers or self.workers[-1].done
	@property
	def progress(self):
		return sum(worker.pos for worker in self.workers)
	@property
	def bps(self):
		if self.done:
			bps = self.size * 8 / max(0.001, time.perf_counter() - self.timestamp)
		else:
			bpss = [worker.bps for worker in self.workers]
			bps = sum(bpss)
			self.max_single_bps = max(self.max_single_bps, max(bpss))
		self.max_bps = max(self.max_bps, bps)
		return bps


_range = range

class ChunkWorker:

	def __init__(self, ctx: ChunkManager, range=None, resp=None, it=None, url=None):
		self.ctx = ctx
		self.running = None
		headers = ctx.headers
		if range is None:
			range = _range(0, min(ctx.size or inf, ctx.size_limit))
		self.range = range
		self.resp = resp
		self.it = it
		self.url = url
		self.headers = headers
		self.pos = 0
		self.timestamp = time.perf_counter()
		self.latency = ctx.latency
		self.last_split = self.timestamp
		self.ttfr = inf
		self.recv_times = []
		self.done = False
		self.error = None
		self.needs_restart = False
		self.restart_cooldown = 5
		self.is_target = False

	async def refresh_resp(self, attempt=0, timeout=None):
		if self.resp is None:
			ctx = self.ctx
			self.timestamp = time.perf_counter()
			self.last_split = self.timestamp
			self.ttfr = inf
			if attempt > 1 or not ctx.request_count & 15:
				self.url = ctx.url
				ctx.session = session = generate_session(False if attempt > 2 else ctx.multiplexed)
			else:
				session = ctx.session
			ctx.sessions.add(session)
			headers = self.headers.copy()
			headers["Priority"] = "i"
			if ctx.allow_range_ends:
				headers["Range"] = f"bytes={self.start + self.pos}-{self.end - 1}"
			else:
				headers["Range"] = f"bytes={self.start + self.pos}-"
			req = session.request(
				ctx.method,
				self.url,
				headers=headers,
				data=ctx.data,
				stream=True,
				verify=ctx.verify,
				timeout=timeout,
			)
			if timeout:
				req = asyncio.wait_for(req, timeout=timeout + 1)
			ctx.request_count += 1
			self.resp = await req
			ait = self.resp.iter_content(CHUNK_SIZE if attempt else CHUNK_SIZE * 8)
			if timeout:
				ait = asyncio.wait_for(ait, timeout=timeout)
			self.it = await ait
			if "Range" in self.headers:
				range_sent = self.headers["Range"].split("=", 1)[-1].split("-", 1)[0]
				range_recv = self.resp.headers["content-range"].split("/", 1)[0].split(None, 1)[-1].split("-", 1)[0]
				assert range_recv == range_sent, "Server failed to serve range header as specified!"
			self.latency = time.perf_counter() - self.timestamp
			self.error = None
			self.needs_restart = False
		self.resp.raise_for_status()
		return self.resp

	def split(self, ratio=0.5):
		offset = self.pos + round(self.remainder * ratio)
		cur_range = range(self.start, self.start + offset)
		new_range = range(self.start + offset, self.end)
		if len(cur_range) < MIN_SPLIT or len(new_range) < MIN_SPLIT:
			self.last_split = inf
			return
		worker = ChunkWorker(
			ctx=self.ctx,
			url=self.url,
			range=new_range,
		)
		self.last_split = worker.last_split
		self.range = cur_range
		self.ctx.workers.append(worker)
		worker.do()

	async def stream(self):
		ctx = self.ctx
		for attempt in itertools.count(1):
			timeout = attempt * 3 + self.latency
			delay = attempt ** 2 + 1 + random.random()
			t = time.perf_counter()
			try:
				await self.refresh_resp(attempt=attempt, timeout=timeout)
				try:
					while True:
						t = time.perf_counter()
						fut = self.it.__anext__()
						content = await asyncio.wait_for(fut, timeout=timeout)
						expected = self.remainder
						if len(content) > expected:
							content = memoryview(content)[:expected]
						length = len(content)
						if length <= 0:
							break
						yield content
						self.pos += length
						self.recv_times.append((t, length))
						if self.pos >= self.size:
							break
						if not isfinite(self.ttfr):
							self.ttfr = time.perf_counter() - self.timestamp
						ctx.update_progress()
						if self.needs_restart:
							raise AttributeError
				except (StopIteration, StopAsyncIteration):
					pass
				break
			except (
				TimeoutError,
				asyncio.TimeoutError,
				niquests.ConnectionError,
				niquests.ConnectTimeout,
				niquests.ReadTimeout,
				niquests.Timeout,
				niquests.exceptions.ChunkedEncodingError,
				AttributeError,
			) as ex:
				if ctx.debug:
					print(repr(ex))
				self.error = ex
			except niquests.HTTPError as ex:
				if ex.response.status_code == 416:
					ctx.allow_range_ends = False
				elif ex.response.status_code not in (408, 420, 429, 502, 503, 504, 509, 522, 599):
					raise
				if ctx.debug:
					print(repr(ex))
				self.error = ex
				await asyncio.sleep(2 ** attempt)
			finally:
				if self.resp is not None:
					try:
						await asyncio.wait_for(self.resp.close(), timeout=delay)
					except (asyncio.TimeoutError, urllib3.exceptions.ResponseNotReady):
						pass
					self.resp = None
				ctx.update_progress(force=True)
			sleep = delay + t - time.perf_counter()
			if sleep > 0:
				await asyncio.sleep(sleep)

	async def run(self):
		self.fp = tempfile.SpooledTemporaryFile(max_size=12 * 1048576)
		async for b in self.stream():
			self.fp.write(b)
		self.fp.flush()
		self.fp.seek(0)
		return self.fp

	def do(self, stream=False):
		if self.running:
			return self.running
		if stream:
			fut = self.stream()
			self.running = fut
		else:
			fut = self.run()
			self.running = asyncio.create_task(fut)
		return self.running

	def split_priority(self, multiplier=1):
		if self.error or self.size <= MIN_SPLIT:
			return 0
		ctx = self.ctx
		ct = time.perf_counter()
		if ct - self.last_split < self.ttfr * 2.5:
			return 0
		concurrency = sum((not worker.done) + bool(worker.error) for worker in ctx.workers)
		if (ct - self.last_split - self.ttfr) * multiplier < concurrency ** 2 / 4:
			return 0
		bps = self.bps
		if bps <= 0:
			return 0
		if self.remainder / self.bps * 8 <= self.ttfr * 2 + 1:
			return 0
		return (ct - self.last_split - self.ttfr) / self.bps * self.remainder / self.size * (1 + self.is_target)

	@property
	def start(self):
		return self.range.start
	@property
	def end(self):
		return self.range.stop
	@property
	def size(self):
		return self.range.stop - self.range.start
	@property
	def remainder(self):
		return self.range.stop - self.range.start - self.pos
	@property
	def bps(self):
		t = time.perf_counter()
		while self.recv_times and t > self.recv_times[0][0] + 5:
			self.recv_times.pop(0)
		if not self.recv_times:
			return 0
		return sum(t[-1] for t in self.recv_times) * 8 / max(0.001, t - self.recv_times[0][0])
	def is_running(self):
		if not self.running:
			return False
		if hasattr(self.running, "done"):
			return not self.running.done()
		return not self.done


async def shatter_request(url, method="get", headers={}, data=None, filename=None, fileobj=None, concurrent_limit=1024, size_limit=1099511627776, verify=None, debug=False, log_progress=True, timeout=None):
	ctx = ChunkManager(
		url=url,
		method=method,
		headers=headers,
		data=data,
		filename=filename,
		fileobj=fileobj,
		concurrent_limit=concurrent_limit,
		size_limit=size_limit,
		verify=verify,
		debug=debug,
		log_progress=log_progress,
		timeout=timeout,
	)
	return ctx.start()
parallel_request = shatter_request


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
	parser.add_argument("-l", "-cl", '--concurrent-limit', help="Limits the amount of concurrent requests; defaults to 64", type=int, required=False, default=64)
	parser.add_argument("-sl", '--size-limit', help="Limits the amount of data to download; defaults to 1099511627776", type=int, required=False, default=1099511627776)
	parser.add_argument("-t", '--timeout', help="Limits the amount of time allowed for the initial request to succeed", type=float, required=False, default=60)
	parser.add_argument("-s", "--ssl", action=argparse.BooleanOptionalAction, default=True, help="Enforces SSL verification; defaults to TRUE")
	parser.add_argument("-d", "--debug", action=argparse.BooleanOptionalAction, default=False, help="Terminates immediately upon non-timeout errors, and writes the response data for errored chunks; defaults to FALSE")
	parser.add_argument("-lp", "--log-progress", action=argparse.BooleanOptionalAction, default=True, help="Continually updates a progress bar in the standard output; defaults to TRUE")
	parser.add_argument("url", help="Target URL")
	parser.add_argument("filename", help='Output filename; use "-" for stdout pipe', nargs="?", default="")
	args = parser.parse_args()
	if not os.path.exists(args.cache_folder):
		os.mkdir(args.cache_folder)
	if os.name == "nt":
		os.system("color")
	ctx = ChunkManager(
		url=args.url,
		headers=json.loads(args.headers),
		filename=args.filename,
		concurrent_limit=args.concurrent_limit,
		size_limit=args.size_limit,
		verify=args.ssl,
		debug=args.debug,
		log_progress=args.log_progress,
		timeout=args.timeout,
	)
	ctx.run()

if __name__ == "__main__":
	main()