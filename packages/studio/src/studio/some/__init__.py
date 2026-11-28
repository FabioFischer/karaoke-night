"""SOME's MIDI extraction inference, vendored for the note boundaries of the melody.

Only what inference needs, from https://github.com/openvpi/SOME at commit
e523eef70777c1370199363de03eb37d66dce76e, under SOME's MIT license (LICENSE in
this folder). Each file names its upstream path and what changed; the tree
mirrors upstream's so the two stay diffable. Everything but utils/slicer2.py
needs torch, so studio.melody imports it lazily. The model weights are not
here: they are CC BY-NC-SA 4.0 and download on first use (docs/PLAN.md §3).
"""
