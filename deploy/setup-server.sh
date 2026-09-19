#!/usr/bin/env bash
set -e

echo "======================================================"
echo " Starting Server Setup for CRM SaaS on 45.195.159.94"
echo "======================================================"

# 1. Install Docker & Docker Compose if not present
if ! command -v docker &> /dev/null; then
  echo "==> Installing Docker and Docker Compose plugin..."
  apt-get update -y
  apt-get install -y docker.io docker-compose-plugin
  systemctl enable docker
  systemctl start docker
  echo "==> Docker installed successfully!"
else
  echo "==> Docker is already installed."
fi

# 2. Setup directory structure
APP_DIR="/var/www/saas"
mkdir -p "$APP_DIR"
mkdir -p "$APP_DIR/deploy"

# 3. Setup PostgreSQL Database and User
echo "==> Configuring PostgreSQL user & database..."
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = 'crm_admin'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER crm_admin WITH ENCRYPTED PASSWORD 'Masters@123';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'solutions'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE solutions OWNER crm_admin;"

sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE solutions TO crm_admin;"

# 4. Clone or update backend repository
if [ ! -d "$APP_DIR/backend/.git" ]; then
  echo "==> Initial cloning of InventoryManagmentBackend..."
  git clone -b prod https://github.com/amanhhiranwal/InventoryManagmentBackend.git "$APP_DIR/backend" || \
  git clone https://github.com/amanhhiranwal/InventoryManagmentBackend.git "$APP_DIR/backend"
else
  echo "==> Backend repo already exists. Fetching latest changes..."
  cd "$APP_DIR/backend"
  git config --global --add safe.directory "$APP_DIR/backend"
  git fetch origin
fi

# 5. Clone or update frontend repository
if [ ! -d "$APP_DIR/e-frontend/.git" ]; then
  echo "==> Initial cloning of InventoryManagmentFrontEnd..."
  git clone -b prod https://github.com/amanhhiranwal/InventoryManagmentFrontEnd.git "$APP_DIR/e-frontend" || \
  git clone https://github.com/amanhhiranwal/InventoryManagmentFrontEnd.git "$APP_DIR/e-frontend"
else
  echo "==> Frontend repo already exists. Fetching latest changes..."
  cd "$APP_DIR/e-frontend"
  git config --global --add safe.directory "$APP_DIR/e-frontend"
  git fetch origin
fi

# 6. Copy production docker-compose file
cp -f "$APP_DIR/backend/deploy/docker-compose.prod.yml" "$APP_DIR/docker-compose.prod.yml" 2>/dev/null || true

# 7. Setup Nginx Virtual Host
echo "==> Configuring Nginx..."
if [ -f "$APP_DIR/backend/deploy/nginx/crm.synergy-global.io.conf" ]; then
  cp -f "$APP_DIR/backend/deploy/nginx/crm.synergy-global.io.conf" /etc/nginx/conf.d/crm.synergy-global.io.conf
fi
nginx -t
systemctl reload nginx
echo "==> Nginx reloaded successfully!"

echo "======================================================"
echo " Server provisioning completed!"
echo " Next step: commit and push branch 'prod' to trigger CI/CD"
echo "======================================================"
