"""核心逻辑自动化测试（标准库 unittest，无需额外依赖）。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exporter  # noqa: E402
import fileutil  # noqa: E402
import scanner  # noqa: E402
import store  # noqa: E402
from job import Job, JobCancelled  # noqa: E402

BASE = 1_700_000_000  # 固定时间基准（秒），避免依赖当前时间


def touch(path: str, content: str = "", mtime: int | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def exported_tree(root: str) -> set[str]:
    """目标目录内的相对文件列表，用于断言没有多出 CSV 清单等额外文件。"""
    found = set()
    for base, _dirs, files in os.walk(root):
        for name in files:
            found.add(os.path.relpath(os.path.join(base, name), root))
    return found


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tfp_scan_")
        self.src = os.path.join(self.tmp, "源 文件夹")
        touch(os.path.join(self.src, "a.txt"), "a", BASE)
        touch(os.path.join(self.src, "b.PNG"), "b", BASE + 60)
        touch(os.path.join(self.src, "c.jpg"), "c", BASE + 120)
        touch(os.path.join(self.src, "sub", "d.md"), "d", BASE + 180)
        touch(os.path.join(self.src, "sub", "deep", "e.log"), "e", BASE + 240)
        touch(os.path.join(self.src, "skip", "f.zip"), "f", BASE + 300)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self, **kwargs):
        return scanner.scan_folder(self.src, job=Job("scan"), **kwargs)

    def names(self, result):
        return sorted(item["name"] for item in result["items"])

    def test_time_range_is_start_inclusive_end_exclusive(self) -> None:
        result = self.scan(start_ms=(BASE + 60) * 1000, end_ms=(BASE + 180) * 1000)
        self.assertEqual(self.names(result), ["b.PNG", "c.jpg"])

    def test_ids_renumbered_after_mtime_desc_sort(self) -> None:
        result = self.scan()
        self.assertEqual([item["id"] for item in result["items"]], list(range(1, 7)))
        self.assertEqual(result["items"][0]["name"], "f.zip")
        self.assertEqual(result["items"][-1]["name"], "a.txt")

    def test_normalize_extensions_handles_fullwidth_separators(self) -> None:
        value = "jpg，pdf；PNG、.JPG\nzip; txt|"
        self.assertEqual(
            scanner.normalize_extensions(value),
            ["jpg", "pdf", "png", "jpg", "zip", "txt"],
        )
        self.assertEqual(scanner.normalize_extensions(None), [])
        self.assertEqual(scanner.normalize_extensions(["PDF", ".Png "]), ["pdf", "png"])

    def test_extension_filter_case_insensitive(self) -> None:
        result = self.scan(extensions="txt,PNG")
        self.assertEqual(self.names(result), ["a.txt", "b.PNG"])

    def test_keyword_filter_case_insensitive(self) -> None:
        result = self.scan(keyword="D")
        self.assertEqual(self.names(result), ["d.md"])

    def test_non_recursive_only_top_level(self) -> None:
        result = self.scan(recursive=False)
        self.assertEqual(self.names(result), ["a.txt", "b.PNG", "c.jpg"])

    def test_pre_cancelled_job_raises(self) -> None:
        job = Job("scan")
        job.cancel()
        with self.assertRaises(JobCancelled):
            scanner.scan_folder(self.src, job=job)

    def test_junction_is_not_followed(self) -> None:
        link = os.path.join(self.src, "loop")
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", link, self.src],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("当前环境无法创建目录联接")
        result = self.scan()
        self.assertEqual(self.names(result), ["a.txt", "b.PNG", "c.jpg", "d.md", "e.log", "f.zip"])
        self.assertLessEqual(result["dirs_seen"], 6)


class ExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tfp_exp_")
        self.src = os.path.join(self.tmp, "src")
        touch(os.path.join(self.src, "a.txt"), "aaa", BASE)
        touch(os.path.join(self.src, "sub", "d.md"), "ddd", BASE + 60)
        touch(os.path.join(self.src, "sub", "deep", "e.log"), "eee", BASE + 120)
        self.items = scanner.scan_folder(self.src, job=Job("scan"))["items"]
        self.by_name = {item["name"]: item for item in self.items}

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_export(self, items, dest, **kwargs):
        return exporter.export_files(items, dest, job=kwargs.pop("job", Job("export")), **kwargs)

    def test_copy_preserve_structure(self) -> None:
        dest = os.path.join(self.tmp, "out1")
        result = self.run_export(self.items, dest)
        self.assertEqual(result["succeeded"], 3)
        self.assertEqual(len(result["exported_ids"]), 3)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(os.path.isfile(os.path.join(dest, "a.txt")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "d.md")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "deep", "e.log")))
        self.assertEqual(exported_tree(dest), {"a.txt", os.path.join("sub", "d.md"), os.path.join("sub", "deep", "e.log")})

    def test_copy_flat(self) -> None:
        dest = os.path.join(self.tmp, "out2")
        result = self.run_export(self.items, dest, preserve=False)
        self.assertEqual(result["succeeded"], 3)
        for name in ("a.txt", "d.md", "e.log"):
            self.assertTrue(os.path.isfile(os.path.join(dest, name)))

    def test_conflict_skip(self) -> None:
        dest = os.path.join(self.tmp, "out3")
        os.makedirs(dest)
        touch(os.path.join(dest, "a.txt"), "old")
        result = self.run_export([self.by_name["a.txt"]], dest, conflict="skip")
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["exported_ids"], [], "被跳过的文件不应标记为已导出")
        with open(os.path.join(dest, "a.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "old")

    def test_conflict_rename(self) -> None:
        dest = os.path.join(self.tmp, "out4")
        os.makedirs(dest)
        touch(os.path.join(dest, "a.txt"), "old")
        result = self.run_export([self.by_name["a.txt"]], dest, conflict="rename")
        self.assertEqual(result["succeeded"], 1)
        self.assertTrue(os.path.isfile(os.path.join(dest, "a (1).txt")))
        with open(os.path.join(dest, "a (1).txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "aaa")

    def test_conflict_overwrite(self) -> None:
        dest = os.path.join(self.tmp, "out5")
        os.makedirs(dest)
        touch(os.path.join(dest, "a.txt"), "old")
        result = self.run_export([self.by_name["a.txt"]], dest, conflict="overwrite")
        self.assertEqual(result["succeeded"], 1)
        with open(os.path.join(dest, "a.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "aaa")

    def test_conflict_overwrite_keeps_old_file_when_copy_fails(self) -> None:
        """覆盖模式不能先删旧文件再复制：复制失败时旧文件必须完好。"""
        from unittest import mock

        dest = os.path.join(self.tmp, "out5b")
        os.makedirs(dest)
        touch(os.path.join(dest, "a.txt"), "old")
        with mock.patch("shutil.copy2", side_effect=OSError("磁盘已满")):
            result = self.run_export([self.by_name["a.txt"]], dest, conflict="overwrite")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["succeeded"], 0)
        with open(os.path.join(dest, "a.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "old", "复制失败不应丢失被覆盖的旧文件")
        leftovers = [n for n in os.listdir(dest) if ".shiji-tmp-" in n]
        self.assertEqual(leftovers, [], f"临时文件未清理：{leftovers}")

    def test_no_temp_files_left_after_success(self) -> None:
        dest = os.path.join(self.tmp, "out5c")
        result = self.run_export(self.items, dest, conflict="overwrite")
        self.assertEqual(result["failed"], 0)
        found = []
        for root, _dirs, files in os.walk(dest):
            found.extend(os.path.join(root, n) for n in files if ".shiji-tmp-" in n)
        self.assertEqual(found, [])

    def test_same_file_is_skipped_and_source_kept(self) -> None:
        result = self.run_export([self.by_name["a.txt"]], self.src)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["succeeded"], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "a.txt")))

    def test_move_removes_source(self) -> None:
        dest = os.path.join(self.tmp, "out6")
        result = self.run_export([self.by_name["d.md"]], dest, mode="move")
        self.assertEqual(result["succeeded"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.src, "sub", "d.md")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "d.md")))

    def test_cancel_keeps_partial_and_no_extra_files(self) -> None:
        dest = os.path.join(self.tmp, "out7")
        job = Job("export")
        job.cancel()
        result = self.run_export(self.items, dest, job=job)
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["processed"], 0)
        self.assertEqual(exported_tree(dest), set(), "取消后目标目录不应有任何文件")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="tfp_store_")
        self.path = os.path.join(self.tmp, "settings.json")
        self.old = store._CONFIG_PATH
        store._CONFIG_PATH = self.path

    def tearDown(self) -> None:
        import shutil
        store._CONFIG_PATH = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_when_missing(self) -> None:
        data = store.load()
        self.assertEqual(data["mode"], "copy")
        self.assertTrue(data["recursive"])

    def test_roundtrip_and_no_tmp_left(self) -> None:
        saved = store.save({"source": "C:\\空 格", "extensions": "pdf，jpg", "preserve": False})
        self.assertEqual(saved, self.path)
        self.assertFalse(os.path.exists(f"{self.path}.{os.getpid()}.tmp"))
        data = store.load()
        self.assertEqual(data["source"], "C:\\空 格")
        self.assertEqual(data["extensions"], "pdf，jpg")
        self.assertFalse(data["preserve"])

    def test_bom_tolerated_and_wrong_type_ignored(self) -> None:
        with open(self.path, "w", encoding="utf-8-sig") as fh:
            fh.write('{"source": "x", "recursive": "yes", "unknown": 1}')
        data = store.load()
        self.assertEqual(data["source"], "x")
        self.assertTrue(data["recursive"])  # 类型不符 → 保留默认
        self.assertNotIn("unknown", data)


class JobTests(unittest.TestCase):
    def test_percent_and_finish(self) -> None:
        job = Job("scan")
        job.update(total=10, processed=5)
        self.assertEqual(job.snapshot()["percent"], 50.0)
        job.finish("done")
        self.assertEqual(job.snapshot()["percent"], 100.0)
        job.check_cancel()

    def test_cancel(self) -> None:
        job = Job("export")
        job.cancel()
        self.assertTrue(job.snapshot()["status"] == "running")
        self.assertTrue(job.cancelled)
        with self.assertRaises(JobCancelled):
            job.check_cancel()


class FileUtilTests(unittest.TestCase):
    def test_long_path_prefix_roundtrip(self) -> None:
        short = os.path.abspath("C:\\tmp")
        self.assertEqual(fileutil.long_path(short), short)
        deep = "\\\\?\\" + os.path.abspath(os.path.join(tempfile.gettempdir(), "x" * 200))
        self.assertEqual(fileutil.long_path(deep), deep)
        self.assertEqual(fileutil.display_path(deep), deep[4:])
        unc = "\\\\?\\UNC\\server\\share\\x"
        self.assertEqual(fileutil.display_path(unc), "\\\\server\\share\\x")

    def test_human_size(self) -> None:
        self.assertEqual(fileutil.human_size(0), "0 B")
        self.assertEqual(fileutil.human_size(1536), "1.5 KB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
