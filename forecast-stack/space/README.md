---
title: Forecast Stack
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
license: mit
short_description: Inspect probability models and export computed snapshots. No API key.
---

# Forecast Stack probability workbench

A keyless demonstration of the open Vaticinus probability engine. Edit a conditional partition, a Bayesian update, a normal threshold model or a stated binary probability. Compute the result and download the exact saved snapshot.

**This is a calculator over stated assumptions, not an AI forecast or a forecasting leaderboard.** Examples are synthetic. There are no model weights, LLM calls, user accounts, persistent prompts or API keys. The app makes no outbound data requests. Hugging Face operates its own hosting/logging infrastructure.

Code: https://github.com/vaticinus/vati/tree/main/forecast-stack

Run locally with Node 22.18+: `node --experimental-strip-types space/server.mts`.
The Docker image runs as the non-root `node` user on port 7860. No GPU required. CPU Basic can sleep while unused.

Contribute a reproducible counterexample, a clearer interface, or an improved model with explicit assumptions. The source repository includes Python baselines, public-data collectors, evaluation and tamper-evident records; the Space is only its interactive probability workbench.
