# Overview

<!-- brief description of what the PR does if it's not clear from the PR title -->

# Required checks before merge

Mark all tasks that were completed with a checkmark. Unrelated tasks can be left unchecked.

PR status:

- [ ] Branch is based on and up-to-date with `main`
- [ ] PR has a clear title and/or brief description
- [ ] PR builders passed
- [ ] No red flags from coderabbit.ai
- [ ] Pinged code owners for human review after all other major items in this list are completed
- [ ] Human approval

Versioning, changelog, and documentation:

- [ ] New features, bugfixes etc are tracked in `CHANGELOG.md`
- [ ] New fields added to existing models are documented in the schemas and related examples added in `api.py`
- [ ] Major updates are also reflected in the `app.description` of `api.py`

Manual tests:

- [ ] General performance did not degrade
- [ ] OpenAPI/Swagger documentation renders correctly
