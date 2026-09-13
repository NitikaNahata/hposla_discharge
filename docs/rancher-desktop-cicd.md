# Rancher Desktop CI/CD Setup Guide

**Purpose:** Use Rancher Desktop locally to test Docker CI/CD pipeline before pushing to GitLab.

**Why Rancher Desktop?**
- Lightweight alternative to Docker Desktop
- Runs Kubernetes (k3s) locally
- Can run Docker images
- Good for testing containerized workflows

---

## **1. Install Rancher Desktop** 📥

### macOS
```bash
# Using Homebrew
brew install rancher-desktop

# Or download from https://rancherdesktop.io/
```

### Linux / Windows
Visit: https://rancherdesktop.io/

---

## **2. Start Rancher Desktop** 🚀

```bash
# Start the application (GUI)
open -a "Rancher Desktop"

# Or from command line:
rancher-desktop start

# Wait for it to be ready (~2-3 minutes)
rancher-desktop shell
# Should see prompt: rancher-desktop%
```

Verify Docker works:
```bash
docker --version
# Docker version 24.x.x...
```

---

## **3. Build Your Image** 🏗️

```bash
# From your project root:
cd /Users/nitikajain/Downloads/final_capstone

# Build the image
docker build -t discharge-copilot:latest .

# Verify build
docker images | grep discharge-copilot
# REPOSITORY           TAG       IMAGE ID      SIZE
# discharge-copilot    latest    abc123...     1.2GB
```

---

## **4. Run Containers Locally** 🐳

### **Option A: Run Single Case**
```bash
# Copy .env.example to .env and add GOOGLE_API_KEY
cp .env.example .env
# Edit .env, add your key

# Run one case
docker run \
  --env-file .env \
  -v $(pwd)/data:/app/data:ro \
  -v $(pwd)/.state:/app/.state \
  discharge-copilot:latest \
  run --case data/samples/case_001.json
```

### **Option B: Run with Docker Compose** (Recommended)
```bash
# Start all services (app + tests + evidence)
docker-compose up

# Or specific service:
docker-compose up copilot
docker-compose up tests
docker-compose up evidence

# View logs:
docker-compose logs -f copilot

# Stop:
docker-compose down
```

### **Option C: Interactive Shell**
```bash
docker run -it \
  --env-file .env \
  -v $(pwd):/app \
  discharge-copilot:latest \
  bash

# Inside container:
python -m discharge_copilot run --case data/samples/case_001.json
```

---

## **5. Test CI/CD Pipeline Locally** 🧪

### **Run Tests in Docker**
```bash
docker run \
  -v $(pwd):/app \
  discharge-copilot:latest \
  pytest -v -m "not live"
```

### **Generate Evidence in Docker**
```bash
docker run \
  --env-file .env \
  -v $(pwd)/data:/app/data:ro \
  -v $(pwd)/evidence:/app/evidence \
  discharge-copilot:latest \
  python scripts/generate_evidence.py
```

### **Run Full Docker Compose Stack**
```bash
# Mirrors CI/CD pipeline
docker-compose up --build

# Output goes to:
# - junit.xml (test results)
# - evidence/ (traces, transcripts)
# - Logs printed to terminal
```

---

## **6. Monitor Container Health** 📊

```bash
# List running containers
docker ps

# View container logs
docker logs <container-id>

# View resource usage
docker stats

# Inspect container
docker inspect discharge-copilot:latest
```

---

## **7. Push to GitLab Container Registry** 🔄

Once local testing passes:

```bash
# Login to GitLab registry
docker login registry.gitlab.com

# Tag image
docker tag discharge-copilot:latest \
  registry.gitlab.com/virtusa/your-repo/discharge-copilot:latest

# Push
docker push registry.gitlab.com/virtusa/your-repo/discharge-copilot:latest

# Verify in GitLab UI → Packages & Registries → Container Registry
```

---

## **8. GitLab CI/CD Pipeline (Docker-Based)** 🔄

### **Create `.gitlab-ci.yml` with Docker**

See [.gitlab-ci.yml](.gitlab-ci.yml) — already set up with:
- Quality checks (linting, type-check) in Docker
- Tests running in Docker
- Evidence generation in Docker
- Multi-stage pipeline

Run locally:
```bash
# Simulate GitLab CI/CD
docker run --rm \
  -v $(pwd):/app \
  -w /app \
  discharge-copilot:latest \
  bash -c "ruff check . && mypy && pytest -v"
```

---

## **9. Troubleshooting** 🔧

### **Container won't start**
```bash
# Check logs
docker logs <container-id>

# Rebuild without cache
docker build --no-cache -t discharge-copilot:latest .
```

### **API key not found**
```bash
# Verify .env file exists
ls -la .env

# Verify key is set
docker run --env-file .env discharge-copilot:latest env | grep GOOGLE_API_KEY
```

### **Volume mount issues**
```bash
# Use absolute paths
docker run -v /Users/nitikajain/Downloads/final_capstone:/app ...

# Or use $(pwd) (current directory)
docker run -v $(pwd):/app ...
```

### **Rancher Desktop won't start**
```bash
# Restart
rancher-desktop shutdown
sleep 5
rancher-desktop start

# Or restart via GUI: Settings → Restart
```

---

## **10. Integration with GitHub Actions** 🚀

Your `.github/workflows/ci.yml` can now use Docker:

```yaml
name: CI

on: [push, pull_request]

jobs:
  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Build Docker image
        run: docker build -t discharge-copilot:latest .
      
      - name: Run tests in Docker
        run: docker run \
          -v $(pwd):/app \
          discharge-copilot:latest \
          pytest -v
      
      - name: Push to registry
        run: |
          docker tag discharge-copilot:latest \
            registry.gitlab.com/virtusa/your-repo/discharge-copilot:latest
          docker push registry.gitlab.com/virtusa/your-repo/discharge-copilot:latest
```

---

## **11. Local Workflow Summary** 📋

```bash
# 1. Make changes
vim src/discharge_copilot/nodes/workers.py

# 2. Test locally in Docker
docker-compose up tests

# 3. Generate evidence
docker run --env-file .env \
  -v $(pwd)/evidence:/app/evidence \
  discharge-copilot:latest \
  python scripts/generate_evidence.py

# 4. Commit
git add -A
git commit -m "feat: improve worker output"

# 5. Push (triggers GitHub Actions + GitLab CI/CD)
git push origin feat/branch-name

# 6. Monitor CI/CD in GitHub / GitLab UI
```

---

## **Quick Commands** ⚡

```bash
# Build
docker build -t discharge-copilot:latest .

# Run single case
docker-compose up copilot

# Run tests
docker-compose up tests

# Run all
docker-compose up --build

# Cleanup
docker-compose down
docker system prune -a

# View logs
docker-compose logs -f

# Shell into container
docker run -it discharge-copilot:latest bash

# Push to registry
docker push registry.gitlab.com/virtusa/your-repo/discharge-copilot:latest
```

---

## **Next: Update GitLab CI to Use Docker** 🔄

See `.gitlab-ci.yml` — it's already configured to:
1. Build Docker image
2. Run tests in containers
3. Generate evidence in containers
4. Push to GitLab Container Registry

Verify it works:
```bash
# Locally simulate GitLab CI jobs
docker build -t discharge-copilot:latest .
docker run discharge-copilot:latest pytest -v
docker run discharge-copilot:latest python scripts/generate_evidence.py
```

---

**You're ready to use Docker + Rancher Desktop for CI/CD!** ✅
