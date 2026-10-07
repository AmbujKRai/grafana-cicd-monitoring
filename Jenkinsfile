// Jenkins pipeline for the TaskFlow API. It mirrors the GitHub Actions workflow so that
// both CI systems can be compared side by side in Grafana. Build metrics (duration,
// result, stage timings, test counts) are exported by the Jenkins Prometheus plugin
// at /prometheus/ and scraped by Prometheus.

def venvPython() {
    return isUnix() ? '.venv/bin/python' : '.venv\\Scripts\\python.exe'
}

def runCmd(String command) {
    if (isUnix()) {
        sh command
    } else {
        bat command
    }
}

def runCmdStatus(String command) {
    return isUnix() ? sh(script: command, returnStatus: true) : bat(script: command, returnStatus: true)
}

pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '50'))
        disableConcurrentBuilds()
    }

    parameters {
        booleanParam(name: 'SIMULATE_SLOW_BUILD', defaultValue: false,
                     description: 'Add a 90 second delay to the test stage (performance-regression demo)')
    }

    environment {
        PYTHONUTF8 = '1'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
    }

    stages {
        stage('Setup') {
            steps {
                runCmd 'python -m venv .venv'
                runCmd "${venvPython()} -m pip install --quiet -r requirements-dev.txt"
            }
        }

        stage('Lint') {
            steps {
                runCmd "${venvPython()} -m ruff check ."
            }
        }

        stage('Unit Tests') {
            steps {
                script {
                    if (params.SIMULATE_SLOW_BUILD) {
                        echo 'Simulating a performance regression (+90 s)'
                        sleep time: 90, unit: 'SECONDS'
                    }
                }
                runCmd "${venvPython()} -m pytest --junitxml=reports/junit.xml --cov=app --cov-report=xml:reports/coverage.xml"
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'reports/junit.xml'
                }
            }
        }

        stage('Security Scan') {
            steps {
                runCmd "${venvPython()} -m bandit -r app -f json -o reports/bandit.json --exit-zero -q"
                // pip-audit exits non-zero when it finds vulnerabilities; the gate below decides
                runCmdStatus "${venvPython()} -m pip_audit -r requirements.txt -f json -o reports/pip-audit.json"
                runCmd "${venvPython()} ci/security_gate.py --bandit reports/bandit.json --pip-audit reports/pip-audit.json"
            }
        }

        stage('Package') {
            steps {
                runCmd "${venvPython()} ci/package.py"
                archiveArtifacts artifacts: 'dist/*.zip', fingerprint: true
            }
        }

        stage('Deploy to Staging') {
            steps {
                // Starts the release candidate locally and runs the API smoke test against it
                runCmd "${venvPython()} ci/smoke_test.py --serve"
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
        }
    }
}
