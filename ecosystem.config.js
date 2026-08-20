module.exports = {
  apps: [{
    name: "ads-collector",
    script: "./main.py",
    interpreter: "./.venv/bin/python",
    instances: 5,
    autorestart: true,
    max_memory_restart: "2G",
  }]
}
