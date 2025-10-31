# StreamShatter
Ever wondered where all the internet bandwidth you get from speedtest websites goes when you're actually trying to download something? Or does your WiFi constantly cut out and ruin your in-progress downloads? Well, either way, here's a tool that may or may not help!

Originally a very basic script for reliably downloading files from servers with inconsistent connections, this project has been revisited and modernised to use https://github.com/jawah/niquests to greatly improve multiplexing performance, for those who still have use for such a tool.

StreamShatter takes advantage of the `Range` HTTP header to dynamically allocate multiple chunks, by starting with one streaming request and gradually bisecting it while bandwidth permits, all without restarting the download. This allows for single, large file downloads from hosts that, whether intentionally or unintentionally, have degraded throughputs. The individual chunks also serve as checkpoints for if/when connections are broken.

# Installation
- Install [python](https://www.python.org) and [pip](https://pip.pypa.io/en/stable/)
- Install StreamShatter as a package:
`pip install streamshatter`

## Usage
```
usage: streamshatter [-h] [-V] [-H HEADERS] [-l CONCURRENT_LIMIT] [-sl SIZE_LIMIT] [-t TIMEOUT] [-s | --ssl | --no-ssl]
                     [-d | --debug | --no-debug] [-lp | --log-progress | --no-log-progress]
                     url [filename]

Multiplexed chunked file downloader

positional arguments:
  url                   Target URL
  filename              Output filename; use "-" for stdout pipe

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  -H, --headers HEADERS
                        HTTP headers, interpreted as JSON
  -l, -cl, --concurrent-limit CONCURRENT_LIMIT
                        Limits the amount of concurrent requests; defaults to 64
  -sl, --size-limit SIZE_LIMIT
                        Limits the amount of data to download; defaults to 1099511627776
  -t, --timeout TIMEOUT
                        Limits the amount of time allowed for the initial request to succeed
  -s, --ssl, --no-ssl   Enforces SSL verification; defaults to TRUE
  -d, --debug, --no-debug
                        Terminates immediately upon non-timeout errors, and writes the response data for errored chunks; defaults to
                        FALSE
  -lp, --log-progress, --no-log-progress
                        Continually updates a progress bar in the standard output; defaults to TRUE
```