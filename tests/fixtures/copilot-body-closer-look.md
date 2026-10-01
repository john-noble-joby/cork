<!-- ccr-overview-v2 -->

## Copilot review overview

### 🔵 Needs a closer look

Malformed successful Pi auth output still reports the provider name as the failure reason.

**Review effort:** Balanced  
**Findings:** None

<details>
<summary><strong>Previously missed (1)</strong></summary>

In code that hasn't changed since last review

<details>
<summary><picture><source media="(prefers-color-scheme: dark)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/medium-v2-dark.svg"><source media="(prefers-color-scheme: light)" srcset="https://github.githubassets.com/static/images/icons/copilot-code-review/medium-v2-light.svg"><img src="https://github.githubassets.com/static/images/icons/copilot-code-review/medium-v2-light.png" alt="Medium severity" width="62" height="18" align="texttop"></picture> Clear Pi probe error detail instead of reporting provider name</summary>

`orchestrate.py:211`

A Pi probe that exits 0 with malformed or non-object JSON is correctly classified as `error`, but this fallback still returns the model's provider as its detail. Preflight consequently prints `pi: unavailable (error: glm-internal)`, contradicting the changelog promise that malformed Pi output has empty detail and misidentifying the provider as the cause. Use a Pi-specific detail parser (or clear fallback detail for error outcomes), and extend the existing rc0 malformed-JSON case to assert an empty detail.
</details>
</details>