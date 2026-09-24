---
title: SpillTrace
emoji: 🛰️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Offline Sentinel-1 oil-spill detection, drift hindcast, vessel ranking
---

# SpillTrace

Satellite oil-spill detection, backward drift to the release point, and explainable
ranking of nearby vessels — one Sentinel-1 SAR scene in, a full case out.

Built for **Smart India Hackathon problem statement 26143** (NTRO). Detection is a
hand-written U-Net (NumPy, forward *and* backward pass, no PyTorch, CPU-only); drift is
a particle hindcast; vessel attribution scores AIS tracks against the release zone.

**Research proof of concept. Human review required. It does not establish responsibility
for a spill.** The AIS traffic and ocean forcing shown are synthetic and labelled as
such; the SAR scene, the model and the metrics are real.

This Space runs the exact offline product — no GPU, no external calls. The committed demo
case opens on the Command Centre; the "New analysis" upload path is inactive here because
there is no raw dataset in the image.
