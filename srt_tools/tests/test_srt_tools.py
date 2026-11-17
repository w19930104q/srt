#!/usr/bin/env python

import io
import os
import subprocess
import sys
import tempfile

try:
    from shlex import quote
except ImportError:  # <3.3 fallback
    from pipes import quote


sample_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "files")


if os.name == "nt":
    # Sigh, shlex.quote quotes incorrectly on Windows
    quote = lambda x: windows_crappy_quote(x)


def run_srt_util(cmd, shell=False, encoding="utf-8-sig"):
    extra_env = {}

    env = {"PYTHONPATH": ".", "SystemRoot": r"C:\Windows"}
    env.update(extra_env)

    raw_out = subprocess.check_output(cmd, shell=shell, env=env)
    return raw_out.decode(encoding)


def windows_crappy_quote(data):
    """
    I'm 100% sure this isn't secure, please don't use it with untrusted code. I
    beg you.
    """
    data = data.replace('"', '""')
    return '"' + data + '"'


def assert_supports_all_io_methods(cmd, exclude_output=False, exclude_stdin=False):
    # TODO: pytype doesn't like the mixed types in the matrix, but this works
    # fine. Maybe it would be happier with a namedtuple?
    cmd[0] = "srt_tools/" + cmd[0]  # pytype: disable=unsupported-operands
    cmd.insert(0, sys.executable)  # pytype: disable=attribute-error
    in_file = os.path.join(sample_dir, "ascii.srt")
    in_file_gb = os.path.join(sample_dir, "gb2312.srt")
    fd, out_file = tempfile.mkstemp()

    # This is accessed by filename, not fd
    os.close(fd)

    outputs = []
    cmd_string = " ".join(quote(x) for x in cmd)

    try:
        outputs.append(run_srt_util(cmd + ["-i", in_file]))
        if not exclude_stdin:
            outputs.append(
                run_srt_util("%s < %s" % (cmd_string, quote(in_file)), shell=True)
            )
        if not exclude_output:
            run_srt_util(cmd + ["-i", in_file, "-o", out_file])
            run_srt_util(
                cmd + ["-i", in_file_gb, "-o", out_file, "-e", "gb2312"],
                encoding="gb2312",
            )
            if not exclude_stdin:
                run_srt_util(
                    "%s < %s > %s" % (cmd_string, quote(in_file), quote(out_file)),
                    shell=True,
                )
                run_srt_util(
                    "%s < %s > %s"
                    % (cmd_string + " -e gb2312", quote(in_file), quote(out_file)),
                    shell=True,
                    encoding="gb2312",
                )
        assert len(set(outputs)) == 1, repr(outputs)

        if os.name == "nt":
            assert "\r\n" in outputs[0]
        else:
            assert "\r\n" not in outputs[0]
    finally:
        os.remove(out_file)


def test_tools_support():
    matrix = [
        (["srt-normalise"], False),
        (["srt-deduplicate"], False),
        (["srt-fixed-timeshift", "--seconds", "5"], False),
        (
            [
                "srt-linear-timeshift",
                "--f1",
                "00:00:01,000",
                "--f2",
                "00:00:02,000",
                "--t1",
                "00:00:03,000",
                "--t2",
                "00:00:04,000",
            ],
            False,
        ),
        (["srt-lines-matching", "-f", "lambda x: True"], False),
        (["srt-process", "-f", "lambda x: x"], False),
        (["srt-mux"], False, True),
        (["srt-mux", "-t"], False, True),
        # Need to sort out time/thread issues
        # (('srt-play'), True),
    ]

    for args in matrix:
        assert_supports_all_io_methods(*args)


MIXED_TAG_BOUNDARY_SRT = """\
1
00:00:01,000 --> 00:00:03,000
<font color="#ff0000">first</font> <custom-tag>line</custom-tag>

2
00:00:02,500 --> 00:00:04,000
<i>overlapping</i> second

3
00:00:00,500 --> 00:00:00,800
tiny

"""


def test_fixed_timeshift_preserves_unknown_tags_and_exports_csv():
    fd, csv_file = tempfile.mkstemp()
    os.close(fd)

    cmd = [
        sys.executable,
        "srt_tools/srt-fixed-timeshift",
        "--seconds",
        "-2",
        "--csv",
        csv_file,
    ]
    env = {"PYTHONPATH": ".", "SystemRoot": r"C:\Windows"}

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )
        raw_out = proc.communicate(MIXED_TAG_BOUNDARY_SRT.encode("utf-8"))[0]
        assert proc.returncode == 0
        out = raw_out.decode("utf-8-sig")

        # Unknown tags survive the shift pipeline...
        assert '<font color="#ff0000">' in out
        assert "<custom-tag>" in out
        assert "<i>overlapping</i>" in out
        # ...negative times are clamped to zero, not dropped...
        assert "00:00:00,000 --> 00:00:00,500" in out
        # ...and the cue shifted entirely before zero is gone.
        assert "tiny" not in out

        # The optional CSV export describes the same cues as the SRT output.
        with io.open(csv_file, encoding="utf-8-sig") as f:
            csv_lines = f.read().splitlines()
        assert csv_lines[0] == "index,start,end,content"
        assert len(csv_lines) == 3  # header + 2 surviving cues
        # Fields containing commas (timestamps, tagged content) are quoted.
        assert '1,"00:00:00,000","00:00:00,500"' in csv_lines[1]
        assert '<font color=""#ff0000"">' in csv_lines[1]
        assert '2,"00:00:00,500","00:00:02,000"' in csv_lines[2]
    finally:
        os.remove(csv_file)
