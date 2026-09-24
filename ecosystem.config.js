module.exports = {
  apps: [{
    name: 'hts-ticket-monitor',

    // Use Python from virtual environment
    script: '.venv/bin/python',
    args: 'app/main.py',
    interpreter: 'none',

    // Process & restart policies
    autorestart: true,
    watch: false,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 5000,
    max_memory_restart: '256M',

    // Graceful shutdown timeout (30 seconds to allow active polling cycle to complete)
    kill_timeout: 30000,

    // Logging configuration
    out_file: 'logs/pm2-out.log',
    error_file: 'logs/pm2-error.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,

    // Environment variables (credentials are loaded from .env via python-dotenv)
    env: {
      PYTHONUNBUFFERED: '1'
    }
  }, {
    name: 'hts-dashboard',

    // Use Python from virtual environment to run dashboard
    script: '.venv/bin/python',
    args: 'scripts/run_dashboard.py',
    interpreter: 'none',

    autorestart: true,
    watch: false,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 5000,
    max_memory_restart: '256M',

    out_file: 'logs/pm2-dashboard-out.log',
    error_file: 'logs/pm2-dashboard-error.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,

    env: {
      PYTHONUNBUFFERED: '1',
      DASHBOARD_HOST: '0.0.0.0',
      DASHBOARD_PORT: '8888'
    }
  }]
};
