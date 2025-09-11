# StreamShatter
Originally a very basic script for reliably downloading files from servers with inconsistent connections, this project has been revisited and modernised to use https://github.com/jawah/niquests to greatly improve multiplexing performance, for those who still have use for such a tool.

StreamShatter takes advantage of the `Range` HTTP header to dynamically allocate multiple chunks, by starting with one streaming request and gradually bisecting it while bandwidth permits, all without restarting the download. This allows for single, large file downloads from hosts that, whether intentionally or unintentionally, have degraded throughputs. The individual chunks also serve as checkpoints for if/when connections are broken.

<video width="960" height="540" muted controls src="https://mizabot.xyz/u/-KLSsIklGJ_wxOHH4xH332ACdm0F/StreamShatter_Demo_-_Made_with_Clipchamp.mp4"></video>
<i>Demo using a normally slow (&lt;1Mbps) server, with simulated network failures at 50%, 90% and 99.5% download progress. No data is lost and the resulting file is intact.</i>

# Installation
- Install [python](https://www.python.org) and [pip](https://pip.pypa.io/en/stable/)
- Install StreamShatter as a package:
`pip install streamshatter`

## Usage
```ini
usage: streamshatter [-h] [-V] [-H HEADERS] [-c CACHE_FOLDER] [-l LIMIT] url [filename]

Multiplexed chunked file downloader

positional arguments:
  url                   Target URL
  filename              Output filename

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  -H, --headers HEADERS
                        HTTP headers, interpreted as JSON
  -c, --cache-folder CACHE_FOLDER
                        Folder to store temporary files
  -l, --limit LIMIT     Limits the amount of chunks to download
```