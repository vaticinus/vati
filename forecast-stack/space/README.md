---
title: Vaticinus Forecast Workbench
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
license: mit
short_description: Forecast with your model or try the free scenario tools.
---

# Vaticinus forecast workbench

Bring a question and the evidence you have. The AI workflow uses your own OpenRouter key; the scenario tools work without one.

[Open the workbench](https://vaticinus-forecast-stack.hf.space) · [Vaticinus](https://vaticinus.com) · [Source code](https://github.com/vaticinus/vati/tree/main/forecast-stack)

## Make a forecast

Enter a future deadline and the rule that will settle the question. Paste your evidence, including source dates and URLs where you have them. The app does not browse or verify those sources.

Choose a model and a spending ceiling between $0.01 and $0.50 per attempt. Direct mode uses one model call. Reviewed mode can use up to four, including a correction. Current OpenRouter prices determine the token-price caps; there is no automatic retry or hosted-key fallback. Extra review has not been shown to improve forecasting accuracy reliably.

You can download the result with its assumptions, evidence and accounted cost. If an attempt fails, it remains a failed attempt with no substituted probability. You can download that record too. Cancelling stops the request but cannot undo charges for work the provider has already processed.

## Try the scenario tools

Change the weights in a two-scenario model, update an estimate with Bayes' rule, or calculate a normal threshold probability. These examples are synthetic. The optional payoff check assumes two outcomes and zero payoff for inaction; it is not investment advice or a decision recommendation.

## Keys and privacy

The app does not save your key in browser storage or a credential file. It sends the key and your input over HTTPS to this Space, which calls OpenRouter. Temporary question and response traces are deleted when the request finishes. Hugging Face, OpenRouter and the selected model provider have their own infrastructure and policies. Do not submit confidential material. A separately limited OpenRouter key is sensible for a public demo.

Brand imagery loads from vaticinus.com. The app has no accounts, analytics integration, durable user history, or automatic external submissions.

## Run locally

With Node 22.18 or newer, from `forecast-stack/`:

```sh
node --experimental-strip-types space/server.mts
```

Open `http://localhost:7860`. The Docker image runs as the non-root `node` user on port 7860. It needs no GPU. Hugging Face currently requires PRO to host this Docker Space on CPU Basic; the owner enabled that subscription separately.

The [evaluation guide](https://github.com/vaticinus/vati/blob/main/forecast-stack/docs/SUPERFORECASTING.md) explains the current evidence. This workbench has not demonstrated superforecaster performance or suitability for major decisions.
