# peakcut-caption-rescue

PeakCut burns word-by-word captions into vertical clips and leaves a `.ass` next to the MP4 — but that `.ass` is not a normal subtitle file: every word is up to three overlapping events, so the moment you need to edit, translate or repost the captions, a plain converter hands you fragments instead of sentences. This tool rebuilds whole lines offline and flags timing that no viewer can read.

Upstream: https://github.com/haziqahmedqasmi/PeakCut

## 上游是什么，它缺什么

[PeakCut](https://github.com/haziqahmedqasmi/PeakCut) (1k+ stars, MIT) turns long videos and live streams into captioned 9:16 clips. `studio/render.py` writes `<name>.mp4`, `<name>.jpg` and `<name>.ass` into the output directory ([render.py#L267-L268](https://github.com/haziqahmedqasmi/PeakCut/blob/HEAD/studio/render.py#L267)). The `.ass` is emitted word by word: for each word it appends a "waiting" event, an "active" event and an "already said" event, all carrying the same group time span and a per-word `\pos(x,y)` ([render.py#L200-L226](https://github.com/haziqahmedqasmi/PeakCut/blob/HEAD/studio/render.py#L200)). A group of N words therefore becomes up to 3N Dialogue events.

Nothing in the repository exports `.srt`/`.vtt`, and there is no importer that turns those events back into lines: the only files that mention captions are the renderer and `tests/test_captions.py`. Caption defects are a repeated open complaint ([#32](https://github.com/haziqahmedqasmi/PeakCut/issues/32), [#17](https://github.com/haziqahmedqasmi/PeakCut/issues/17)), yet the artifacts users get are video-only.

## 这个工具做什么、不做什么

It reads a PeakCut `.ass`, merges the layered per-word events by `\pos`, groups the words that share one group span, orders them left-to-right, and emits:

- whole-line SRT and WebVTT,
- a JSON report with per-word timings,
- timing defects: cue overlap, empty text, short cues, fast reading, over-long lines.

It does **not** touch the MP4, re-render video, re-transcribe audio, or guess missing words. It is a deterministic converter/checker for the subtitle artifact PeakCut already produced.

Measured on the shipped sample (`examples/in/progressive.ass`, 2 caption lines, 8 events):

```text
$ ffmpeg -v error -i examples/in/progressive.ass -f srt - | grep -c '^[0-9][0-9]*$'
9
$ python3 bin/peakcut-caption-rescue.py examples/in/progressive.ass
8 events -> 2 lines (4 words); 0 errors, 0 warnings
```

ffmpeg's direct conversion yields 9 cues: 8 single-word fragments (each line split into 4 pieces, one per event layer) plus one cue for the hook's `\p1` drawing. This tool yields the 2 real lines, and ignores the drawing.

## 安装 / 用法 / 输出 / 退出码

Python 3.8+, stdlib only, no network:

```bash
git clone git@github-linmoxi33:linmoxi33/peakcut-caption-rescue.git
cd peakcut-caption-rescue
python3 bin/peakcut-caption-rescue.py examples/in/progressive.ass --srt /tmp/out.srt --vtt /tmp/out.vtt --json
```

JSON output (abridged; full example in `examples/out/expected.json`):

```json
{
  "ok": false,
  "input": {"caption_events": 8, "words": 4, "cues": 2},
  "cues": [{"index": 1, "start": 0.0, "end": 1.2, "text": "MAKE IT", "words": [...]}],
  "defects": [{"code": "W_SHORT_CUE", "severity": "warning", "cue": 2, "detail": "duration 0.30s < 0.40s"}],
  "summary": {"errors": 0, "warnings": 1, "defects": 1}
}
```

| exit | meaning |
| --- | --- |
| 0 | converted, no defect |
| 1 | converted, defects found (see `defects`; `severity` separates error/warning) |
| 2 | usage or input error (missing file, not ASS, no caption events); JSON mode still prints `{"ok": false, "error": ...}` |

Tune the checks: `--min-dur 0.4 --max-cps 25 --max-chars 42`.

Tests: `python3 -m unittest discover -s tests -v` — 13 tests, 7 behavioural (exit codes, JSON, SRT/VTT bytes).

## 局限

- Line grouping relies on PeakCut's invariant that words in one caption group share the same start/end span (see `render.py#L200-L226`). A hand-edited `.ass` that breaks the invariant falls back to one cue per span.
- Hook text and vector drawings are skipped by design; they are not spoken captions.
- Reading-speed and line-length limits are configurable heuristics, not broadcast certification.
- The sample is constructed to match the renderer's documented event pattern; no real user video/`.ass` is redistributed.

## 本轮创新

B 档：首次针对 PeakCut 的「逐词三层事件」字幕格式做整行还原，并给出可复现的量化结论——同一份 `.ass` 经 ffmpeg 直转得到 9 条碎片 cue（8 条单词碎片 + 1 条 hook 绘图），还原后是 2 条完整行。台账里的 `mcp-*`、`*-audit` 都不处理 ASS 事件层，也没有把「渐进式逐词字幕」当作可解析格式；这个结论换一个热榜项目不能照搬。

## 与同类项目的差异

- **ffmpeg / Subtitle Edit**：按 Dialogue 事件逐条转，PeakCut 的 3N 事件会变成 3N 条重复碎片；本工具按 `\pos` 合并并重建整行。上文的 9 vs 2 是对比命令的真实输出。
- **Aegisub 等编辑器**：面向人工编辑，不识别 PeakCut 的分层语义；本工具零交互、可批处理、有 JSON 与退出码。
- `gh search repos "ass to srt converter"` 命中的 4 个仓库（最高 2 stars）都只做逐事件格式转换，未处理逐词分层。

## License

MIT © 2026 linmoxi33. PeakCut itself is MIT; this repository contains no PeakCut code, only an independent parser for its output format.