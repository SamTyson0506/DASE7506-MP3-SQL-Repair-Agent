Development-only model experiments (not deployed)

The final submitted agent is CPU-only and loads no language model, adapter, or learned selector.

The included training-config.json, training-log.jsonl, data-provenance.json and training-status.json document an exploratory LoRA experiment on Qwen/Qwen3-0.6B at revision c1899de289a04d12100db370d81485cdf75e47ca. Supervision came exclusively from the 480 training tasks, their official training-memory gold SQL, schema and real original-query tool feedback.

Only LoRA A/B matrices were trained. Two complete epoch checkpoints were saved. The original four-epoch learning-rate schedule was interrupted during early epoch 3 to free laptop memory for CPU evaluation; partial epoch-3 updates were discarded. The saved epoch-2 adapter did not improve validation over the CPU controller, and is not used by the submitted method. The source export excludes all unused adapter/base weights and the derived training dataset.

An unchanged public Qwen/Qwen3.5-0.8B model (revision 2fc06364715b967f1860aea9cf38778875588b17) was also tested with offline CPU inference and bounded tool feedback. Its full test score was 191/240, below the selected controller. It is not a runtime dependency.

Small training-only bag/set selectors were evaluated and discarded. The final method uses a conservative structural rule for joined name projections, with every changed SQL checked through the official tool. No validation or test labels were used to train any model or classifier.

These records disclose development assistance and unsuccessful experiments. They are not instructions to load experimental weights, alter benchmark files, or use the model submission route for the final CPU agent.
