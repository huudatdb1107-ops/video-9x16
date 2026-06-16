# LLM Fill Fallback

During the pipeline, step 2 (`fill_content.json`) calls an LLM to convert the raw script into the structured `content.json`. If the LLM request fails (network issue, API quota, or malformed response), the pipeline stops with the message:

```
⚠ LLM fill failed. Boss fill manual rồi chạy lại với --skip-llm
```

**How to recover**
1. Edit the `script.txt` (or provide a new script) as needed.
2. Re‑run the pipeline with the flag `--skip-llm` so the step is bypassed and the existing `content.json` (or a manually created one) is used.
3. If you need a fresh `content.json`, you can create it manually following the template schema located in `templates/01_Text/content_schema.json`.

**Tip**: When you anticipate LLM failures, always keep a backup of the last successful `content.json` in the workspace.
