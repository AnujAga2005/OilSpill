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

This deployment runs the exact product — no GPU, no external calls. The committed cases open
on the Command Centre instantly. The **New analysis** screen is live: upload a Sentinel-1
GeoTIFF of your own — a scene over 24 MiB is chunked automatically so it clears the platform's
32 MiB request cap. The raw training dataset is not in the image.
