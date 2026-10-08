"""Offline checks for weak-network progress preservation and safe LPV extraction."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

spec=importlib.util.spec_from_file_location('download_ue4',Path(__file__).resolve().parents[1]/'scripts/download_ue4.py')
downloader=importlib.util.module_from_spec(spec);spec.loader.exec_module(downloader)

class Reply(io.BytesIO):
    def __init__(self,data,status=200,headers=None):
        super().__init__(data);self.status=status;self.headers=headers or {'ETag':'"stable"'}


def archive_bytes(files=None):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as zipped:
        for name,data in (files or {'UnrealEngine-4.27/Engine/Shaders/Private/LPVCommon.ush':'reference'}).items():
            zipped.writestr(name,data)
    return stream.getvalue()


class DownloadChecks(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target=Path(self.directory.name)/'source.zip'
        self.partial=self.target.with_suffix('.zip.part')

    def run_download(self,responses):
        with patch.object(downloader.urllib.request,'urlopen',side_effect=responses),patch.object(downloader.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            return downloader.download('http://test.invalid/source.zip',self.target,attempts=len(responses),rate_mib=0)

    def test_range_resume(self):
        data=archive_bytes();self.partial.write_bytes(data[:30])
        self.run_download([Reply(data[30:],206,{'ETag':'"stable"','Content-Range':f'bytes 30-{len(data)-1}/{len(data)}'})])
        self.assertEqual(self.target.read_bytes(),data)

    def test_complete_partial_needs_no_new_authorization(self):
        data=archive_bytes();self.partial.write_bytes(data)
        with patch.object(downloader.urllib.request,'urlopen') as fetch,contextlib.redirect_stdout(io.StringIO()):
            downloader.download('http://test.invalid',self.target,rate_mib=0)
            fetch.assert_not_called()
        self.assertEqual(self.target.read_bytes(),data)

    def test_ignored_range_replays_saved_prefix(self):
        data=archive_bytes();self.partial.write_bytes(data[:40])
        self.run_download([Reply(data)])
        self.assertEqual(self.target.read_bytes(),data)

    def test_interruption_retains_bytes_for_retry(self):
        data=archive_bytes()
        self.run_download([Reply(data[:50]),Reply(data)])
        self.assertEqual(self.target.read_bytes(),data)

    def test_mismatching_prefix_never_overwrites_progress(self):
        self.partial.write_bytes(b'user progress')
        with self.assertRaises(ValueError):self.run_download([Reply(archive_bytes())])
        self.assertEqual(self.partial.read_bytes(),b'user progress')

    def test_expired_authorization_keeps_progress(self):
        self.partial.write_bytes(b'prefix')
        with self.assertRaises(RuntimeError):
            self.run_download([urllib.error.HTTPError('private URL',404,'Not found',{},None)])
        self.assertEqual(self.partial.read_bytes(),b'prefix')

    def test_extract_only_lpv_and_reject_traversal(self):
        self.target.write_bytes(archive_bytes({'UE/Engine/Shaders/Private/LPVCommon.ush':'LPV',
                                               'UE/Other.cpp':'unrelated'}))
        dest=Path(self.directory.name)/'references'
        selected=downloader.extract_lpv(self.target,dest)
        self.assertEqual(selected,['Engine/Shaders/Private/LPVCommon.ush'])
        self.assertFalse((dest/'Other.cpp').exists())
        self.target.write_bytes(archive_bytes({'UE/../escape/LPVCommon.ush':'unsafe'}))
        with self.assertRaises(ValueError):downloader.extract_lpv(self.target,dest)


if __name__=='__main__':unittest.main(verbosity=2)
