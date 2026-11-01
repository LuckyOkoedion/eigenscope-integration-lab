# Test automatic model integration

Run actual causal-model inference before and after attaching EigenScope. The model runner checks automatic profile detection, fresh preparation, cached preparation, projection activity and generation latency across a model matrix.

Start with [EigenScope Integrations](https://eigenscope.guardianai.network/?view=view-sdk) for your SDK and protected-runtime setup. Use the [package registry](https://eigenscope.guardianai.network/registry) for the distribution matching your deployment. For an additional model or custom checkpoint, visit [Sponsored R&D](https://eigenscope.guardianai.network/?view=view-partnerships).

## Install the model runner

Model inference uses a separate environment so the normal industry scenarios remain lightweight. From the repository root:

```sh
uv venv --python 3.12 .venv-models
uv pip install --python .venv-models/bin/python -r requirements-model-inference.txt
uv pip install --python .venv-models/bin/python --no-deps -e .
.venv-models/bin/eigenscope-lab models --engine-smoke --output results/model-engine.json
```

On Windows, use `.venv-models\Scripts\python.exe` and `.venv-models\Scripts\eigenscope-lab.exe` in place of the corresponding `bin` paths.

On Intel macOS, use a supported Linux CPU environment for current PyTorch packages. An existing Colima daemon is suitable; select it with `docker --context colima` and follow the Linux installation steps inside your development container. Keep model volumes private and mount checkpoints read-only. There is no need to change the global Docker context or restart Colima.

The engine smoke command runs genuine small GPT-2 and Llama models initialized locally, with no model download or EigenScope credential. Its report uses `mode: model-engine-smoke` and `eigenscope_projection_tested: false`. Passing engine checks return exit code 0. Use your actual checkpoints and connector for EigenScope profile and projection results.

Install your deployment's official SDK and connector into this environment using its documented package source. Keep credentials in the environment or your secret manager. The lab does not place credentials in a matrix or pass them on the command line.

## Choose your model matrix

Copy [the example matrix](../examples/model-matrix.example.json) to the ignored results folder:

```sh
cp examples/model-matrix.example.json results/model-matrix.json
```

Replace the two placeholder paths with local causal-model checkpoints. Include at least two architecture families when evaluating support across models. Use short synthetic prompts and labels that you are comfortable including in a report.

Each model has an `id`, a `reference`, an optional local `revision`, a `prompt` and a bounded `max_new_tokens`. For a Hugging Face repository reference, provide its immutable 40-character commit revision. Branch names such as `main` are unsuitable for repeatable comparisons. Local checkpoints are fingerprinted from their model assets.

Connector maintainers can use the public helper to obtain the same local-checkpoint identity:

```python
from eigenscope_lab.model_engine import checkpoint_fingerprint

revision = checkpoint_fingerprint(model.config.name_or_path)
```

The helper returns `sha256:` followed by the hexadecimal digest. It hashes `config.json` first, then the checkpoint's top-level `*.safetensors` files sorted by filename. For each file, it streams the UTF-8 filename, a NUL byte and the file bytes into SHA-256. Compute this once during preparation. Remote checkpoints use their pinned 40-character commit revision.

Model loading uses the local cache by default. Add `--allow-downloads` to permit model downloads through Transformers. Access to gated models follows the model owner's normal authorization process. Keep model files and caches outside this repository.

```sh
.venv-models/bin/eigenscope-lab models \
  --matrix results/model-matrix.json \
  --connector your_connector:connect \
  --repeat 5 \
  --warmup 1 \
  --device cpu \
  --output results/models.json
```

Use `cuda` or `mps` when supported by your installed PyTorch build and hardware. Compare results on the same device, model precision, prompt, token count and software versions.

A matrix accepts up to eight models. `--repeat` accepts 1–100 measured runs, `--warmup` accepts 0–10 warmups, and `max_new_tokens` accepts 1–64. Start with the small example workload before increasing repetitions.

## Connect the actual SDK

The lab's connector is a small adapter around your SDK's automatic model-connection API. SDK maintainers should ship this connector in the SDK package so developers can select the provided callable directly. This keeps the runner compatible with SDK releases without guessing method names or hardcoding Gemma, a layer number or an SAE dictionary. The connector performs the real SDK/runtime calls; it must not substitute a local projection simulator.

Pass an installed Python callable as `--connector package.module:connect`. Importing that module executes its Python code, so use a connector you maintain or trust. Its callable contract is:

```python
def connect(model, *, cache_dir, refresh):
    # Invoke your SDK's automatic connection API for this loaded model.
    # Return a session implementing the contract below.
    ...
```

The lab supplies the loaded model object, a new private temporary cache directory and the preparation mode. It does not supply a model ID, selected hook, basis files or profile ID. The directory is outside the checkout, is owner-only and is removed after the run. Keep protected profile files inside the runtime or this private cache, according to your deployment's design.

The first connection uses `refresh=True` to exercise preparation in an empty cache. The lab closes that session and connects again with the same cache and `refresh=False` to exercise reuse.

The returned session exposes these members:

| Member | Contract |
| --- | --- |
| `receipt` | Dictionary described below, produced after actual preparation and attachment |
| `set_mode("identity")` | Configure the SDK controls to preserve the incoming activation |
| `set_mode("protected")` | Apply the deployment's configured protection controls |
| `snapshot()` | Return cumulative hook/projection counters and measured projection timings |
| `close()` | Detach hooks and release the session's resources; leave the model usable and support repeated calls |

The receipt contains only the versioned compatibility and preparation fields:

```text
schema_version: 1
model_type: detected architecture name
model_revision: revision or local fingerprint matching the loaded checkpoint
hidden_size: detected activation width
profile_digest: 64 hexadecimal SHA-256 characters
signature_verified: true only after the runtime verifies its profile
events: detected, cache_miss or cache_hit, verified, loaded
```

Map the SDK's actual metadata to this contract. The connector reports signature verification and preparation events from the runtime's real behavior. The lab checks the receipt's consistency with the loaded model and observed lifecycle; signature verification itself is performed by the SDK/runtime that owns the protected assets.

`snapshot()` returns cumulative `hook_calls`, `projected_calls`, `changed_calls` and `provision_count` integers, plus a `projection_ms` list of finite nonnegative measurements. Advance `changed_calls` only when the projected activation actually differs from its input. Include only completed projection durations. Do not include activations, prompts, profile contents, paths, credentials or raw network responses.

## What a run checks

For each model, the runner executes:

1. Baseline greedy generation through the actual model, before attaching a connector.
2. Fresh automatic preparation and the model/profile compatibility checks.
3. Untimed identity and protected probes observing the actual attached forward hooks.
4. Identity-mode generation, comparing generated token IDs with the baseline in memory.
5. Protected generation, checking finite output, projection activity and changed activations.
6. Hook cleanup, including on failure.
7. Cached preparation and repetition of the same integration checks.

The default Transformers engine adds temporary observers before and after the attached instance forward hooks. It checks that identity controls preserve activations, protected controls change them, and tensor shapes, dtypes, devices and values remain valid. Activation values stay in memory. Observers are removed before timed runs, including after a failed probe. These native probes support Tensor, tuple and list outputs; pre-hook and fused execution backends can provide an engine observation adapter. The report identifies native observations separately from connector counters.

Both fresh and cached sessions are validated. The reported integrated generation and projection percentiles use the cached session; preparation reports fresh and cached durations separately. Warmups are separate from timed samples. Inference must use the already prepared profile: a provisioning event during measured generation fails the lifecycle check. Missing prerequisites remain `BLOCKED`; a rejected expectation remains `FAIL`. Without a connector, a successful baseline can pass while its integration suite remains `BLOCKED`.

For task and privacy effectiveness, pair this matrix with held-out, independently scored prompts for your domain. Record expected answers, private spans and permitted output before running them. An activation changing establishes intervention; task preservation and private-information handling require their own output checks. Use [the live SDK/API tests](live-testing.md) for text protection and owned-mapping lifecycle contracts.

## Read the latency measurements

Reports keep these boundaries separate:

| Measurement | Boundary |
| --- | --- |
| Fresh preparation | Automatic profile resolution, verification, loading and attachment in the new cache |
| Cached preparation | Attachment using the same prepared cache |
| Generation wall time | After tokenization and initial input-device transfer, through completed prefill and bounded greedy decoding |
| Time to first token | The same generation start through the first completed generated token |
| Tokens per second | Generated-token count divided by total generation time |
| Projection duration | Runtime measurements supplied by the connector for completed projections |

The engine uses a fixed-length, synchronous greedy loop with KV caching. It completes the requested token count even if the model emits an EOS token. Device synchronization and completed-token checks are included in the measured loop. Tokenization, the initial input transfer and a model-serving HTTP request are outside this boundary.

Compare baseline, identity and protected generation on the same workload. These measurements characterize the runner's loop; benchmark your serving engine separately when evaluating its production throughput. The report carries sample counts and percentile summaries; use enough repeats for the percentile you need and preserve failed runs when reviewing results. Report complete measured inference overhead alongside the runtime's projection timing.

To enforce your deployment's performance budget, add any of these options:

| Option | Enforced expectation |
| --- | --- |
| `--max-projection-p95-ms` | Maximum p95 projection duration in milliseconds |
| `--max-wall-overhead-pct` | Maximum protected-generation wall-time overhead relative to baseline |
| `--max-ttft-overhead-pct` | Maximum protected time-to-first-token overhead relative to baseline |

Budget values must be finite and positive. The report identifies which budgets were enforced; without a supplied budget it reports measurements rather than inventing a latency target. Compare p50, p95 and p99 using the recorded sample counts and identical workloads.

## Keep results and profiles private

Generated JSON belongs in ignored `results/`. Reports use an explicit field allowlist and include checks, timing summaries and model fingerprints. Generated text, token IDs, prompts, model paths, profile digests, full profile receipts, raw exceptions and credentials are not exported.

Do not commit private connectors, checkpoints, profile files, model caches, credentials or production results. Before sharing a result, review its labels and measurements. Contribute generic fixtures and connector-contract improvements through [the project contribution guide](../CONTRIBUTING.md).

## Implementation references

- [Transformers model loading](https://huggingface.co/docs/transformers/en/main_classes/model) documents configuration, checkpoints and loading behavior.
- [Transformers generation](https://huggingface.co/docs/transformers/en/main_classes/text_generation) documents generation controls and KV caching.
- [PyTorch CUDA synchronization](https://docs.pytorch.org/docs/stable/generated/torch.cuda.synchronize.html) and [MPS synchronization](https://docs.pytorch.org/docs/stable/generated/torch.mps.synchronize.html) define device completion boundaries.
