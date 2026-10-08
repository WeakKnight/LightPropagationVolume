"""Download GitHub ZIPs safely over unreliable links; extract only LPV references.

Read an authenticated codeload URL from stdin: never persist it or print it.
A server honoring Range resumes directly. GitHub codeload may return 200 instead:
replay and compare the saved prefix, then append. Retries never truncate progress.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import socket
import time
import urllib.error
import urllib.request
import zipfile


def lpv_file(name):
    p = PurePosixPath(name)
    stem = p.stem.lower()
    return (p.suffix.lower() in {'.usf','.ush','.cpp','.h','.cs','.uplugin'} and
            ('lpv' in stem or 'lightpropagationvolume' in stem or
             'LightPropagationVolume' in p.parts))


def extract_lpv(archive, destination):
    destination = Path(destination).resolve()
    selected = []
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            if not lpv_file(entry.filename) or entry.is_dir():
                continue
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or len(path.parts) < 2:
                raise ValueError('Unsafe archive path')
            relative = Path(*path.parts[1:])
            target = (destination/relative).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('Unsafe extraction target')
            # Reading verifies CRC for each retained reference file.
            data = zipped.read(entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            selected.append(str(relative))
    if not selected:
        raise ValueError('Archive contains no LPV reference files')
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'lpv-manifest.json').write_text(json.dumps(selected,indent=2)+'\n')
    return selected


def complete_archive(partial,target,metadata,state):
    with zipfile.ZipFile(partial) as zipped:
        if not zipped.infolist(): raise ValueError('Empty archive')
    partial.replace(target)
    with target.open('rb') as stream:
        sha=hashlib.file_digest(stream,'sha256').hexdigest()
    state.update(bytes=target.stat().st_size,sha256=sha,complete=True)
    metadata.write_text(json.dumps(state,indent=2)+'\n')
    print(f'Complete: {state["bytes"]/2**20:.1f} MiB; SHA256 {sha}',flush=True)
    return target


def download(url, target, attempts=8, rate_mib=4):
    target = Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        with zipfile.ZipFile(target) as zipped:
            if not zipped.infolist(): raise ValueError("Existing archive is empty")
        print("Using existing complete archive",flush=True)
        return target
    partial = target.with_suffix(target.suffix+'.part')
    metadata = target.with_suffix(target.suffix+'.download.json')
    state = json.loads(metadata.read_text()) if metadata.exists() else {}
    if partial.exists():
        try:
            return complete_archive(partial,target,metadata,state)
        except zipfile.BadZipFile:
            pass
    block_size = 256*1024
    for attempt in range(1,attempts+1):
        offset = partial.stat().st_size if partial.exists() else 0
        print(f'Attempt {attempt}/{attempts}: saved {offset/2**20:.1f} MiB',flush=True)
        headers = {'User-Agent':'LPV-reference-downloader','Accept-Encoding':'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
            if state.get('etag'): headers['If-Range'] = state['etag']
        try:
            request = urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(request,timeout=45) as response:
                etag = response.headers.get('ETag')
                if state.get('etag') and etag and state['etag'] != etag:
                    raise ValueError('Archive changed; keep partial and use a different target')
                if etag:
                    state['etag'] = etag
                    metadata.write_text(json.dumps(state,indent=2)+'\n')
                resumed = response.status == 206
                if resumed and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                    raise ValueError('Server returned an inconsistent Range response')
                replay = offset if not resumed else 0
                if replay: print('Server ignores Range; checking saved prefix before appending.',flush=True)
                transferred = 0
                start = time.monotonic()
                report_at = offset + 20*2**20
                with partial.open('a+b') as output, partial.open('rb') as prefix:
                    while True:
                        block = response.read(block_size)
                        if not block: break
                        transferred += len(block)
                        if rate_mib > 0:
                            delay = transferred/(rate_mib*2**20) - (time.monotonic()-start)
                            if delay > 0: time.sleep(min(delay,1))
                        if replay:
                            check = min(replay,len(block))
                            if prefix.read(check) != block[:check]:
                                raise ValueError('Saved prefix differs; refusing to corrupt archive')
                            replay -= check
                            block = block[check:]
                        if block:
                            output.write(block)
                            output.flush()
                            offset += len(block)
                            if offset >= report_at:
                                print(f'Saved {offset/2**20:.1f} MiB',flush=True)
                                report_at = offset + 20*2**20
                if replay:
                    raise ConnectionError('Connection ended before saved prefix was replayed')
            # EOCD is required: an interrupted stream cannot be called a ZIP.
            return complete_archive(partial,target,metadata,state)
        except (urllib.error.HTTPError,urllib.error.URLError,socket.timeout,ConnectionError,OSError,zipfile.BadZipFile) as error:
            # No URLs/auth tokens in errors or metadata.
            code = getattr(error,'code',None)
            print(f'Interrupted: {type(error).__name__}'+(f' HTTP {code}' if code else ''),flush=True)
            if code in (401,403,404):
                raise RuntimeError('Download authorization expired or is unavailable; partial preserved') from None
            if attempt < attempts: time.sleep(min(5*attempt,30))
    raise RuntimeError('Retry budget exhausted; partial preserved')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path,default=Path('references/downloads/UnrealEngine-4.27.zip'))
    parser.add_argument('--extract',type=Path,default=Path('references/UnrealEngine-4.27'))
    parser.add_argument('--attempts',type=int,default=8)
    parser.add_argument('--rate-mib',type=float,default=4)
    parser.add_argument('--extract-only',action='store_true')
    args=parser.parse_args()
    import math
    if not 1 <= args.attempts <= 100 or not math.isfinite(args.rate_mib) or not 0 <= args.rate_mib <= 1024:
        parser.error('Expected attempts 1..100 and finite rate 0..1024 MiB/s')
    if args.extract_only:
        archive=args.target
    else:
        import sys
        url=sys.stdin.readline().strip()
        if not url.startswith('https://codeload.github.com/EpicGames/UnrealEngine/zip/'):
            parser.error('Provide the authenticated EpicGames codeload HTTPS URL on stdin')
        archive=download(url,args.target,args.attempts,args.rate_mib)
    selected=extract_lpv(archive,args.extract)
    print(f'Extracted {len(selected)} LPV files to {args.extract}',flush=True)


if __name__=='__main__':
    main()
