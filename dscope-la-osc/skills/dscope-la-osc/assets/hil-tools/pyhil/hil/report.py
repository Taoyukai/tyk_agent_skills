"""Markdown + plot report generation for HIL test runs."""

import os
import subprocess
import sys
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import expect


def plot_channels(cap, ch_list=None, path="waveform.png", t_max=None,
                  offset=0, title=None):
    """Plot logic channels (digital steps) to a PNG file."""
    chs = ch_list if ch_list is not None else list(range(len(cap.channels)))
    n = len(chs)
    if n == 0 or len(cap) == 0:
        return None
    offset = max(0, min(offset, len(cap) - 1))
    ns = len(cap) if t_max is None else min(len(cap), int(t_max * cap.samplerate))
    ns = min(ns, len(cap) - offset)
    t = np.arange(offset, offset + ns) / cap.samplerate * 1000.0  # ms
    fig, axes = plt.subplots(n, 1, figsize=(10, 0.6 * n + 0.8), sharex=True,
                             squeeze=False)
    for k, ch in enumerate(chs):
        sig = cap.ch(ch)[offset:offset + ns].astype(np.int8)
        ax = axes[k][0]
        ax.step(t, sig, where="post", lw=0.8)
        ax.set_ylim(-0.3, 1.3)
        ax.set_yticks([0, 1])
        # label: channel name resolved from the metadata (physical index
        # selectors are common; channels[] is capture-ordered)
        try:
            from .capture import _resolve_ch
            idx = _resolve_ch(cap.meta, cap.channels, ch)
            label = cap.channels[idx]
        except Exception:
            label = str(ch)
        ax.set_ylabel(str(label), fontsize=7)
        ax.grid(True, alpha=0.3)
    axes[-1][0].set_xlabel("time (ms)")
    if title:
        fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def plot_analog(cap, ch_list=None, scale=1.0, path="analog.png", title=None):
    chs = ch_list if ch_list is not None else list(range(len(cap.channels)))
    t = np.arange(len(cap)) / cap.samplerate * 1000.0
    fig, axes = plt.subplots(figsize=(10, 3), squeeze=False)
    ax = axes[0][0]
    for ch in chs:
        v = cap.volts(ch, scale)
        ax.plot(t, v, lw=0.8, label=str(cap.channels[ch]))
    ax.set_xlabel("time (ms)")
    ax.set_ylabel("V")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
    if title:
        fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def generate(out_path, title, meta_lines=None, plots=None):
    """Write a markdown report with all recorded check results."""
    rs = expect.results()
    npass = sum(1 for r in rs if r.passed)
    lines = []
    lines.append(f"# {title}\n")
    lines.append(f"*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}*\n")
    if meta_lines:
        for m in meta_lines:
            lines.append(f"- {m}")
        lines.append("")
    verdict = "✅ PASS" if npass == len(rs) and rs else "❌ FAIL"
    lines.append(f"**Result: {verdict} — {npass}/{len(rs)} checks passed**\n")
    lines.append("| # | Check | Result | Detail |")
    lines.append("|---|-------|--------|--------|")
    for i, r in enumerate(rs, 1):
        lines.append(f"| {i} | `{r.name}` | {'✅' if r.passed else '❌'} | {r.detail} |")
    lines.append("")
    if plots:
        for p in plots:
            rel = os.path.relpath(p, os.path.dirname(out_path) or ".")
            lines.append(f"![{os.path.basename(p)}]({rel})\n")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path
