# Contributing Guide

Thanks for contributing.

## Development Setup

1. Clone the repository.
2. Create and activate a virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Local Validation

Run the CLI help and a dry preview on a sample PDF:

```bash
python watermark_remover.py -h
python watermark_remover.py sample.pdf -p
```

## Pull Request Rules

1. Keep changes focused and explain why they are needed.
2. Include before/after behavior in the PR description.
3. For detection logic changes, include at least one sample case.
4. Do not include confidential PDFs in commits.

## Suggested Contribution Areas

- Detection improvements for uncommon watermark styles
- Safer removal logic for edge cases
- Performance optimization on large multi-page PDFs
- Documentation and examples

