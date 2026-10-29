from app.cluster import cluster_findings, normalize_quote
from app.ingest import chunks_for_bundle, file_stats, load_bundle, parse_timestamp, silence_findings
from app.verify import verify_quote
from tools.make_incident import make_incident


def test_timestamp_parsing_and_truth_exclusion(tmp_path):
    assert parse_timestamp("2026-10-01T12:00:00Z INFO ready") == "2026-10-01T12:00:00Z"
    assert parse_timestamp("not a timestamp") is None
    root = make_incident("xid79_demo", tmp_path, 7)
    bundle = load_bundle(root)
    assert all(file.path != "truth.json" for file in bundle.files)
    assert "truth.json" not in bundle.by_path
    rank = bundle.by_path["logs/rank_013.log"]
    assert (rank.rank, rank.node) == (13, 1)
    stats = {item.file: item for item in file_stats(bundle)}
    assert stats["logs/rank_013.log"].first_ts is not None
    assert stats["logs/rank_013.log"].last_step == 289


def test_chunks_split_on_lines_and_keep_absolute_line_numbers(tmp_path):
    root = tmp_path / "bundle"
    (root / "logs").mkdir(parents=True)
    long_line = "x" * 180
    (root / "logs/rank_000.log").write_text("\n".join([long_line] * 400) + "\n")
    bundle = load_bundle(root)
    chunks = chunks_for_bundle(bundle)
    assert len(chunks) > 1
    assert chunks == chunks_for_bundle(bundle)
    assert chunks[0].line_start == 1
    assert chunks[-1].line_end == 400
    assert all(chunk.est_tokens <= 16_000 for chunk in chunks)
    seen = [int(line[1:6]) for chunk in chunks for line in chunk.text.splitlines()]
    assert seen == list(range(1, 401))


def test_silence_finding_is_only_rank_013_for_demo(tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    findings = silence_findings(bundle)
    assert [item["file"] for item in findings] == ["logs/rank_013.log"]
    finding = findings[0]
    assert finding["kind"] == "silence"
    assert finding["severity"] == 4
    assert verify_quote(bundle, finding["file"], finding["line_start"], finding["line_end"], finding["quote"])


def test_verifier_accepts_whitespace_normalization_and_rejects_bad_ranges(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "scheduler.log").write_text("2026-10-01T12:00:00Z ERROR   node stalled\nnext detail\n")
    bundle = load_bundle(root)
    assert verify_quote(bundle, "scheduler.log", 1, 1, "ERROR node stalled")
    assert verify_quote(bundle, "scheduler.log", 1, 2, "node stalled next detail")
    assert not verify_quote(bundle, "scheduler.log", 1, 1, "node stalled next detail")
    assert not verify_quote(bundle, "scheduler.log", 0, 1, "ERROR")
    assert not verify_quote(bundle, "scheduler.log", 1, 2, "x" * 201)
    assert not verify_quote(bundle, "missing.log", 1, 1, "ERROR")


def test_watchdog_lines_dedupe_and_earliest_exemplar_is_kept(tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    findings = []
    for source in bundle.files:
        if source.kind != "rank_log":
            continue
        for number, line in enumerate(source.lines, 1):
            if "Watchdog caught collective operation timeout" in line:
                findings.append({"file": source.path, "line_start": number, "line_end": number,
                                 "quote": line.split(" ERROR ", 1)[1], "kind": "timeout",
                                 "severity": 3})
    clusters = cluster_findings(findings, bundle)
    assert len(findings) == 63
    assert len(clusters) == 1
    assert clusters[0]["count"] == 63
    assert clusters[0]["files_total"] == 63
    assert len(clusters[0]["files"]) == 10
    assert len(clusters[0]["exemplars"]) == 3
    assert clusters[0]["exemplars"][0]["file"] == "logs/rank_000.log"
    assert normalize_quote("rank=13 node-01 fault at 0xABC 79") == normalize_quote("rank=14 node-02 fault at 0x123 88")


def test_cluster_cap_preserves_singleton_high_severity():
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    findings = [{"file": f"f{i}.log", "line_start": 1, "quote": f"failure {name}",
                 "kind": "error", "severity": 5 if i == 5 else 1} for i, name in enumerate(names)]
    clusters = cluster_findings(findings, limit=2)
    assert len(clusters) == 2
    assert any(item["exemplars"][0]["quote"] == "failure foxtrot" for item in clusters)
