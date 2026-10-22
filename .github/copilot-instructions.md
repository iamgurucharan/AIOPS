# Workspace Guidance

- This project is a Python standard-library HTTP server with a plain HTML/CSS/JavaScript dashboard.
- Keep local secrets in ignored `config.env`; never copy credentials from chat or terminal transcripts into the repository.
- Kubernetes deployment resources must stay in the `guru` namespace; the running app must not have Kubernetes API credentials.
- Robot Shop integration is a read-only HTTP probe to the configured existing LoadBalancer URL, not a cross-namespace Kubernetes query.
- ServiceNow Problem and Knowledge records must only be created through explicit approval actions.
- Validate Python changes with `python -m unittest discover -s tests` and `python -m py_compile server.py`.