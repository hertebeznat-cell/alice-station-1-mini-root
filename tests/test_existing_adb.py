import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from station_root.workflow import Workflow


class ExistingADBTests(unittest.TestCase):
    def test_authorized_standard_server_is_reused_and_never_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/"keys/adbkey";key.parent.mkdir();key.write_text("test-only")
            flow=Workflow(log=lambda line:None);flow.adb_binary=Mock(return_value="adb")
            def run(command,**kwargs):
                if "devices" in command:output="List of devices attached\nspeaker device\n"
                elif "cat /proc/device-tree/compatible" in command:output="amlogic"
                elif command[-1]=="id":output="uid=0(root) gid=0(root)"
                else:self.fail("Unexpected ADB operation: "+repr(command))
                return subprocess.CompletedProcess(command,0,output,"")
            socket=Mock();socket.__enter__=Mock(return_value=socket);socket.__exit__=Mock(return_value=False);socket.connect_ex.return_value=0
            with patch("station_root.workflow.socket.socket",return_value=socket),patch("station_root.workflow.subprocess.run",side_effect=run) as runner:
                self.assertEqual(flow.verify({"work":folder}),"speaker")
            self.assertTrue(all(call.args[0][2]=="5037" for call in runner.call_args_list))
            self.assertFalse(any("kill-server" in call.args[0] for call in runner.call_args_list))
