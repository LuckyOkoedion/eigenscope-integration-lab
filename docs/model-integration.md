# Test automatic model integration

Run actual causal-model inference before and after attaching EigenScope. The model runner checks automatic profile detection, fresh preparation, cached preparation, projection activity and generation latency across a model matrix.

Start with [Model Providers & Enterprise AI Platforms](https://eigenscope.guardianai.network/guide/model-platforms) and [EigenScope Integrations](https://eigenscope.guardianai.network/integrations#model-connection). The [official package registry](https://eigenscope.guardianai.network/registry) supplies the SDK. Use [provider engineering](https://eigenscope.guardianai.network/sponsored-rd?vertical=MODEL_PROVIDERS_ENTERPRISE_AI&usecase=model-platforms) for additional architectures, custom checkpoints, inference engines, hardware integration and private deployments.

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

## Install the official SDK and prepare the protected runtime

SDK **0.2.5** ships both `eigenscope_sdk.connect_model` and the lab adapter
`eigenscope_sdk.lab_connector:connect`. Install its immutable official wheel:

```sh
uv pip install --python .venv-models/bin/python \
  'https://eigenscope.guardianai.network/pypi/packages/eigenscope_sdk-0.2.5-py3-none-any.whl#sha256=ac5485b605244ba513e0e99c9f7b2eb04f5499e7c08ff505803cc8afdf14d061'
```

The deployment operator prepares the authorized Linux model environment once.
Use the protected operator kit supplied for that deployment, outside this
checkout. It includes `bootstrap_model_hook_runtime.py`, the authorized runtime
wheel, its `SHA256SUMS`, and `BOOTSTRAP.txt`. The separately provisioned signed
profile catalog and profile key stay in protected backend storage.

Run the kit's actual bootstrap with paths and the reviewed wheel digest supplied
by your operator. The shell variables below identify those protected files;
they contain no key values:

```sh
.venv-models/bin/python -B "$EIGENSCOPE_OPERATOR_KIT/bootstrap_model_hook_runtime.py" \
  --wheel "$EIGENSCOPE_RUNTIME_WHEEL" \
  --expected-wheel-sha256 "$EIGENSCOPE_RUNTIME_SHA256" \
  --catalog "$EIGENSCOPE_PROFILE_CATALOG" \
  --profile-key-file "$EIGENSCOPE_PROFILE_KEY_FILE" \
  --env-output "$EIGENSCOPE_RUNTIME_ENV"

. "$EIGENSCOPE_RUNTIME_ENV"
```

The bootstrap verifies Linux permissions and memory-lock limits, checks the
prepared framework versions, installs the hash-checked runtime wheel and
authenticates the provisioned profiles. `observed-runtime-versions.json` records
the kit's environment; use your operator's dependency installation plan to
prepare it. Model identity is then checked when the application connects its
loaded model.

The prepared checkpoint combinations are:

| Checkpoint | Precision | Attention implementation |
| --- | --- | --- |
| `google/gemma-2-2b` | `bfloat16` | `eager` |
| `EleutherAI/pythia-70m` | `float32` | `sdpa` |

Use the exact checkpoint revision and framework environment supplied with its
matching profile. Keep tokenizer/preprocessing assets paired with that checkpoint.
For another precision, attention implementation, adapter or device, use the
[provider engineering route](https://eigenscope.guardianai.network/sponsored-rd?vertical=MODEL_PROVIDERS_ENTERPRISE_AI&usecase=model-platforms).

## Choose your model matrix

Copy [the example matrix](../examples/model-matrix.example.json) to the ignored results folder:

```sh
cp examples/model-matrix.example.json results/model-matrix.json
```

Replace the two placeholder paths with local causal-model checkpoints. Include at least two architecture families when evaluating support across models. Use short synthetic prompts and labels that you are comfortable including in a report.

Each model has an `id`, a `reference`, an optional local `revision`, a `prompt` and a bounded `max_new_tokens`. For a Hugging Face repository reference, provide its immutable 40-character commit revision. Branch names such as `main` are unsuitable for repeatable comparisons. Local checkpoints are fingerprinted from their model assets.

Set an optional per-model `dtype` to `auto`, `float32`, `float16` or `bfloat16`
when selecting checkpoint precision. For example, add `"dtype": "bfloat16"` to
a model entry when that checkpoint, device and matching runtime profile support
BF16. `auto` uses the checkpoint's framework precision selection. Omitting the
field preserves the framework's normal loading behavior. Baseline, identity and
protected inference use the same loaded model; the report records its actual
precision with each inference measurement and checks that it remains unchanged.

An optional per-model `attn_implementation` selects `eager` or `sdpa` through
Transformers' maintained model loader. Omit it to preserve the framework's normal
selection. Use the implementation supported by the checkpoint and its matching
runtime profile. The report records the loaded framework attention implementation
and checks that baseline, identity and protected measurements keep the same one.
For example, a model entry may contain both `"dtype": "bfloat16"` and
`"attn_implementation": "eager"` when that is its supported deployment combination.

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
  --connector eigenscope_sdk.lab_connector:connect \
  --repeat 5 \
  --warmup 1 \
  --device cpu \
  --output results/model-release-validation.json
```

Use `cuda` or `mps` when supported by your installed PyTorch build and hardware. Compare results on the same device, model precision, prompt, token count and software versions.

A matrix accepts up to eight models. `--repeat` accepts 1–100 measured runs, `--warmup` accepts 0–10 warmups, and `max_new_tokens` accepts 1–64. Start with the small example workload before increasing repetitions.

## Connect the actual SDK

Your application passes its already loaded model to the official SDK once:

```python
from eigenscope_sdk import connect_model

with connect_model(model) as connection:
    outputs = model(**inputs)
    print(connection.observations())
```

Keep the normal inference call used by your serving application. Connection
setup automatically resolves the matching model/profile identity, authenticates
the profile, warms its device hook and attaches it. Projection runs in-process
on that model's device. Use `connection.set_protection(False)` for an explicit
baseline control, `connection.set_protection(True)` to restore protection, and
`connection.close()` for cleanup. The context manager closes it automatically.

For the release-validation command above, the packaged lab adapter supplies the
following session contract. It calls this same SDK connection API and maps the
runtime's actual preparation events and completed projection measurements.

The lab supplies the loaded model object, a new private temporary cache directory
and the preparation mode. The SDK resolves the model ID, selected hook and profile
automatically. The directory is outside the checkout, is owner-only and is removed
after the run. Keep protected profile files in the runtime or this private cache.

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

The official adapter maps the SDK's actual metadata to this contract. Signature
verification and preparation events come from the runtime's completed behavior.
The lab checks the receipt against the loaded model and observed lifecycle.

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

For the two-model example, a successful release-validation report contains six
passing suites: baseline, integration and privacy for each checkpoint. Review the
named checks and preserve failed results alongside the run's SDK, framework,
precision and attention versions. This report is written to the ignored
`results/model-release-validation.json` file.

## Verify automatic privacy and task preservation

Use natural, untagged prompts for model-privacy evaluation on every deployment checkpoint. Include realistic synthetic API keys, customer/person names, account references, email addresses, internal routes and financial figures alongside public task values such as cloud regions, programming languages, status codes, product tiers, cities and JSON schemas. Store the expected sensitive spans and permitted output in the test oracle; do not identify them with labels or wrappers in the input.

Under `Task=1, Private=0`, inspect the actual generated output against three independent expectations:

- **Removal (`R`):** the original sensitive values are absent.
- **Public values (`P`):** the required public task values remain intact.
- **Task (`T`):** the requested answer and output structure, including JSON keys and types where specified, are preserved.

Keep generated text and activation values in memory. Export the named checks and measurements without the values themselves. A failed removal, public-value or structure expectation fails the model/privacy evaluation. Native hook observations establish that the intervention occurred; generated-output checks establish its effect on the requested workflow. Run these checks on Gemma and the second architecture family through the same official SDK connection.

Ordinary checkpoint matrix runs include four natural-language R/P/T cases in a
separate `model:<id>:privacy` suite. The cached connection generates an identity
control and a protected response for each case. The identity control must
actually reproduce the specified sensitive values while preserving the public
values and requested structure; this prevents an unresponsive model from
appearing to provide privacy. The protected response must pass all three output
checks. A failing privacy suite also fails that model's integration suite.
These semantic probes run separately from the measured inference population.
They stop at the model's EOS token or a maximum of 192 newly generated tokens;
only those generated tokens are decoded for evaluation. The local engine smoke
command remains an inference-mechanics check.

All published model/privacy prompts are natural and untagged, with validation that rejects cue words and wrappers before execution. Sensitive-value retention is a `P0` failure. Use the [zero-tag SDK/API challenges](live-testing.md#automatic-zero-tag-privacy-challenges) for automatic text protection and owned-mapping lifecycle checks.

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
