#!/usr/bin/env bash
set -e

REPO_DIR="/home/kali/Downloads/AI/youtube-autopilot"
cd "$REPO_DIR"

GH_BIN="$HOME/.local/bin/gh"
if ! command -v "$GH_BIN" &> /dev/null; then
    GH_BIN="gh"
fi

echo "============================================================"
echo "  YouTube AI Autopilot — GitHub Cloud Autonomous Deployment"
echo "============================================================"

# Check if gh is authenticated
if "$GH_BIN" auth status >/dev/null 2>&1; then
    echo "[✓] GitHub CLI is authenticated!"
    GH_USER=$("$GH_BIN" api user -q .login 2>/dev/null || echo "")
    echo "[*] Logged in as: $GH_USER"
    
    # Check if repo already exists on user's GitHub
    REPO_EXISTS=false
    if "$GH_BIN" repo view "$GH_USER/youtube-autopilot" >/dev/null 2>&1; then
        REPO_EXISTS=true
        echo "[*] Repository $GH_USER/youtube-autopilot already exists."
    fi
    
    if [ "$REPO_EXISTS" = false ]; then
        echo "[*] Creating new public GitHub repository: youtube-autopilot..."
        "$GH_BIN" repo create youtube-autopilot --public --source=. --remote=origin --description "Autonomous $0/month YouTube AI Autopilot Cloud Platform with Arabic Web Dashboard and Automated Video Production"
    else
        echo "[*] Setting remote origin..."
        git remote set-url origin "https://github.com/$GH_USER/youtube-autopilot.git" || git remote add origin "https://github.com/$GH_USER/youtube-autopilot.git"
    fi

    echo "[*] Pushing branch 'main' to origin..."
    git branch -M main
    git push -u origin main --force

    echo "[✓] Code pushed successfully to GitHub!"
    echo "[*] Cloud Workflows available in: https://github.com/$GH_USER/youtube-autopilot/actions"
    exit 0
else
    # Check SSH connection
    if ssh -T -o StrictHostKeyChecking=no git@github.com 2>&1 | grep -q "successfully authenticated"; then
        echo "[✓] GitHub SSH key is authenticated!"
        git push -u origin main --force
        echo "[✓] Code pushed successfully via SSH!"
        exit 0
    else
        echo "[!] Not authenticated yet with GitHub."
        exit 1
    fi
fi
