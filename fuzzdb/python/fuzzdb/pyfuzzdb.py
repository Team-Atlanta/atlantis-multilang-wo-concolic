import html
import json
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


class CovInfo:
    def __init__(self, func_name, src, lines):
        self.func_name = func_name
        self.src = src
        self.lines = lines

    def __str__(self):
        return f"func_name: {self.func_name}, src: {self.src}, lines: {self.lines}"


@dataclass
class Seed:
    name: str
    directory: Path
    created_time: int


DUMMY_SUMMARY = {
    "branches": {"count": 0, "covered": 0, "notcovered": 0, "percent": 0},
    "functions": {"count": 0, "covered": 0, "percent": 0},
    "instantiations": {"count": 0, "covered": 0, "percent": 0},
    "lines": {"count": 0, "covered": 0, "percent": 0},
    "mcdc": {"count": 0, "covered": 0, "notcovered": 0, "percent": 0},
    "regions": {"count": 0, "covered": 0, "notcovered": 0, "percent": 0},
}
COV_HTML_HEAD = "<head><meta name='viewport' content='width=device-width,initial-scale=1'><meta charset='UTF-8'><link rel='stylesheet' type='text/css' href='%s'></head>"
COV_HTML_TBL_HEAD = "<div class='source-name-title'><pre>%s</pre></div><tr><td><pre>Line</pre></td><td><pre>Count</pre></td><td><pre>Source</pre></td><td><pre>Finder</pre></td></tr>"


def cov_tbl_elem(line_num, n_cover, code, finders):
    code = html.escape(code, quote=True)
    ret = f"<tr><td class='line-number'><a name='L{line_num}' href='#L{line_num}'><pre>{line_num}</pre></a></td>"
    if n_cover == 0:
        ret += "<td class='uncovered-line'></td>"
    else:
        ret += f"<td class='covered-line'><pre>{n_cover}</pre></td>"
        code = f"<span class ='green'>{code}</span>"
    ret += f"<td class='code'><pre>{code}</pre></td>"
    finders = ", ".join(finders)
    ret += f"<td><pre>{finders}</pre></td></tr>"
    return ret


class FuzzDB:
    def __init__(self, conf_path):
        with open(conf_path, "r") as f:
            conf = json.load(f)
        self.cov_dir = Path(conf["cov_dir"])
        self.corpus_dir = Path(conf["corpus_dir"])
        self.pov_dir = Path(conf["pov_dir"])
        self.harness_name = conf["harness_name"]

        self.node_covs = {}

    # Keep this for mlla
    def list_seeds(self) -> list[str]:
        corpus = os.listdir(str(self.corpus_dir))
        covs = os.listdir(str(self.cov_dir))
        return list(
            map(
                lambda x: x[:-4],
                filter(lambda x: x.endswith(".cov") and x[:-4] in corpus, covs),
            )
        )

    def list_seeds_new(self) -> List[Seed]:
        corpus = os.listdir(str(self.corpus_dir))
        pov = os.listdir(str(self.pov_dir))
        covs = os.listdir(str(self.cov_dir))

        all_seeds = []
        for fname in covs:
            if fname.endswith(".cov"):
                seed_name = fname[:-4]
                if seed_name in corpus:
                    all_seeds.append(
                        Seed(name=seed_name, directory=self.corpus_dir, created_time=-1)
                    )
                elif seed_name in pov:
                    all_seeds.append(
                        Seed(name=seed_name, directory=self.pov_dir, created_time=-1)
                    )
        return all_seeds

    def load_node_cov(self, seed_name: str) -> dict[str, CovInfo]:
        if seed_name in self.node_covs:
            return self.node_covs[seed_name]
        cov_file = self.cov_dir / f"{seed_name}.cov"
        try:
            with open(cov_file) as f:
                data = json.load(f)
                covs = {}
                for func_name in data:
                    d = data[func_name]
                    covs[func_name] = CovInfo(func_name, d["src"], d["lines"])
                self.node_covs[seed_name] = covs
                return covs
        except:
            return {}

    def load_seed_metadata(self, seed: Seed):
        try:
            with open(seed.directory / f".{seed.name}.metadata") as f:
                return json.load(f)
        except:
            return {"finder": "unknown"}

    def load_func_cov(self, seed_name) -> list[str]:
        return list(self.load_node_cov(seed_name).keys())

    def load_raw_cov(self, seed_name) -> list[int]:
        cov_name = self.cov_dir / seed_name
        if not cov_name.exists():
            return []
        ret = []
        with open(cov_name, "rb") as f:
            while True:
                tmp = f.read(4)
                if len(tmp) != 4:
                    break
                tmp = struct.unpack("<I", tmp)[0]
                ret.append(tmp)
        return ret

    def check(self):
        for seed in self.list_seeds_new():
            for func_name, info in self.load_node_cov(seed.name).items():
                if not Path(info.src).exists():
                    print(info.src, "does not exist")
                assert Path(info.src).exists()

    def save_eval_result(self, out_dir: Path, eval_time: int):
        seed_dir = out_dir / "seeds"
        cov_dir = out_dir / "uniafl_cov"
        pov_dir = out_dir / "povs"
        report_dir = out_dir / "reports" / self.harness_name
        for d in [seed_dir, cov_dir, pov_dir, report_dir]:
            os.makedirs(d, exist_ok=True)

        seed_creation_times = self.__get_seed_creation_times()
        created_time_dict = {
            seed_name: seed.created_time
            for seed_name, seed in seed_creation_times.items()
        }
        (out_dir / f"{self.harness_name}_seed_creation_time.json").write_text(
            json.dumps(created_time_dict)
        )
        cov_over_time = self.__dump_cov_over_time(
            seed_creation_times, out_dir, eval_time
        )

        # Create Crash report
        crash_json = out_dir / f"{self.harness_name}_crash.json"
        cmd = f"python3 -m libCRS.submit show --harness {self.harness_name}"
        cmd += f" --format json --for-vd-eval > {crash_json}"
        os.system(cmd)

        # Copy raw data
        for src, dst in [
            (self.corpus_dir, seed_dir),
            (self.cov_dir, cov_dir),
            (self.pov_dir, pov_dir),
        ]:
            os.system(f"cp -r {src} {dst / self.harness_name}")
            os.system(f"rm -f {dst / self.harness_name / '.*'} > /dev/null 2>&1")
        workdir = os.environ.get("CRS_WORKDIR", None)
        assert workdir != None
        os.system(f"cp {workdir}/submit/submit.db {out_dir}/submit.db")

        # Save summary
        line_covs = self.__load_all_line_covs()
        summary = self.__create_summary(line_covs)
        summary_file = out_dir / f"{self.harness_name}_summary.json"
        summary_file.write_text(json.dumps(summary))

        # Run coverage helper
        os.system(f"cp /home/crs/static/style.css {report_dir}/style.css")
        cmd = "python3 /home/crs/code_coverage/coverage_utils.py post_process"
        cmd += f" -output-dir {report_dir}"
        cmd += f" -src-root-dir /"
        cmd += f" -summary-file {summary_file}"
        os.system(cmd)
        self.__create_cov_htmls(report_dir / "coverage", line_covs)

        # Finalize report
        final = Path("/home/crs/static/cov_graph.html").read_text()
        report = report_dir / "linux/report.html"
        if report.exists():
            final = "<div class='container'>" + report.read_text() + "</div>" + final
        os.system(f"cp {cov_over_time} {report_dir / 'linux/cov.json'}")
        os.system(f"cp {crash_json} {report_dir / 'linux/crash.json'}")
        report.write_text(final)

    def __dump_cov_over_time(
        self, seed_creation_times: dict[str, Seed], out_dir: Path, end_time: int
    ) -> Path:
        time_map = {0: []}
        for seed_name, seed in seed_creation_times.items():
            t = seed.created_time
            if t not in time_map:
                time_map[t] = []
            time_map[t].append(seed)
        ret = []
        covs = set()
        for t in sorted(time_map.keys()):
            finders = []
            for seed in time_map[t]:
                name = seed.name
                finders.append(self.load_seed_metadata(seed)["finder"])
                for cov in self.load_raw_cov(name):
                    covs.add(cov)
            ret.append({"time": t, "cov": len(covs), "finders": finders})
        ret.append({"time": end_time, "cov": len(covs), "finders": []})
        ret = json.dumps(ret)
        output = out_dir / f"{self.harness_name}_cov.json"
        output.write_text(ret)
        return output

    def __get_seed_creation_times(self) -> dict[str, Seed]:
        ret = {}
        start_time = int(os.environ.get("START_TIME", None))
        assert start_time != None
        for seed in self.list_seeds_new():
            seed_name = seed.name
            seed_directory = seed.directory
            ct = os.path.getctime(str(seed_directory / seed_name)) - start_time
            seed.created_time = ct
            ret[seed_name] = seed
        return ret

    def __load_all_line_covs(self):
        line_covs = {}
        for seed in self.list_seeds_new():
            covs = self.load_node_cov(seed.name)
            finder = self.load_seed_metadata(seed)["finder"]
            for cov in covs.values():
                cov.src = str(Path(cov.src).resolve())
                if cov.src not in line_covs:
                    line_covs[cov.src] = {}
                for line in cov.lines:
                    if not line in line_covs[cov.src]:
                        line_covs[cov.src][line] = {"n_cover": 0, "finder": set()}
                    line_covs[cov.src][line]["n_cover"] += 1
                    line_covs[cov.src][line]["finder"].add(finder)
        return line_covs

    def __create_summary(self, line_covs):
        ret = {"type": "llvm.coverage.json.export", "version": "2.0.1"}
        files = []
        total = DUMMY_SUMMARY.copy()
        for src in line_covs:
            if not Path(src).exists():
                continue
            summary = DUMMY_SUMMARY.copy()
            count = len(Path(src).read_text().split("\n"))
            covered = len(line_covs[src])
            percent = float(covered * 100) / float(count)
            total["lines"]["count"] += count
            total["lines"]["covered"] += covered
            summary["lines"] = {"count": count, "covered": covered, "percent": percent}
            file = {"filename": src, "summary": summary}
            files.append(file)
        ret["data"] = [{"files": files, "total": total}]
        return ret

    def __create_cov_htmls(self, report_cov_dir, line_covs):
        for src_path, cov in line_covs.items():
            src_path = Path(src_path)
            ret = self.__create_cov_html(src_path, cov)
            target = Path(str(report_cov_dir) + str(src_path) + ".html")
            os.makedirs(str(target.parent), exist_ok=True)
            target.write_text(ret)

    def __create_cov_html(self, src_path, line_covs):
        ret = "<html>"
        n = len(str(src_path).split("/")) - 1
        ret += COV_HTML_HEAD % ("../" * n + "style.css")
        ret += "<body>"
        ret += "<h2>Coverage Report</h2><div class='centered'><table>"
        ret += COV_HTML_TBL_HEAD % (str(src_path))
        line_num = 0
        for code in src_path.read_text().split("\n"):
            line_num += 1
            if line_num in line_covs:
                n_cover = line_covs[line_num]["n_cover"]
                finder = line_covs[line_num]["finder"]
            else:
                n_cover = 0
                finder = []
            ret += cov_tbl_elem(line_num, n_cover, code, sorted(finder))
        ret += "</table></div></body></html>"
        return ret


if __name__ == "__main__":
    FuzzDB(sys.argv[1]).check()
