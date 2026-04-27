# Git SSH Key Note

Always use the explicit deploy key reference for GitHub operations in this repo:

```sh
GIT_SSH_COMMAND='ssh -i /root/.ssh/github_deploy_key_20260427121153 -o IdentitiesOnly=yes' git pull
GIT_SSH_COMMAND='ssh -i /root/.ssh/github_deploy_key_20260427121153 -o IdentitiesOnly=yes' git push
```
