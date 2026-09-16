"""Run the actual device storage transactions on POSIX with injected failures."""
import subprocess
import shlex
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]
APP=ROOT/'platforms/esp32-p4-wifi6-touch-lcd/sd_uploader'


@pytest.fixture(scope='module')
def storage_harness(tmp_path_factory):
    output=tmp_path_factory.mktemp('storage-build')/'faults'
    flags=subprocess.check_output(['pkg-config','--cflags','--libs','openssl'],text=True).strip()
    subprocess.run(['cc','-g','-fsanitize=address,undefined','-Wno-deprecated-declarations',
                    '-DSDU_HOST_TEST','-DSDU_ROOT="."','-I'+str(APP/'test'),'-I'+str(APP/'main'),
                    str(APP/'main/storage.c'),str(APP/'main/protocol.c'),
                    str(APP/'test/storage_faults.c'),*shlex.split(flags),'-o',str(output)],check=True)
    return output


@pytest.mark.parametrize('scenario',range(13),ids=['success','short-write','flush','sync',
    'close','read','rename','published-corruption','abort-cleanup','deinit','unmount','no-space',
    'reserved-cleanup'])
def test_storage_fault(storage_harness,tmp_path,scenario):
    subprocess.run([str(storage_harness),str(scenario)],cwd=tmp_path,check=True,capture_output=True)
