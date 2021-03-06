pipeline {
    agent none
    options {
        timeout(time: 10, unit: 'MINUTES')
        buildDiscarder(logRotator(
            artifactNumToKeepStr: '32',
            numToKeepStr: '64'
        ))
    }
    stages {
        stage('Run mypy typechecks') {
            agent {
                dockerfile {
                    filename 'docker/test/Dockerfile'
                    additionalBuildArgs '--build-arg TAG=3.8-buster'
                }
            }
            steps {
                sh '''
                mypy src --junit-xml testing/gw-unit-reports/mypy-report.xml
                '''
            }
            post {
                always {
                    junit 'testing/gw-unit-reports/mypy-report.xml'
                }
            }
        }

        stage('Run python2 unittests') {
            agent {
                dockerfile {
                    filename 'docker/test/Dockerfile'
                    additionalBuildArgs '--build-arg TAG=2.7-stretch'
                }
            }
            steps {
                sh '''
                ./testing/unittests/run.sh
                '''
            }
            post {
                always {
                    junit 'testing/gw-unit-reports/*.xml'
                }
            }
        }

        stage('Run python3 unittests') {
            agent {
                dockerfile {
                    filename 'docker/test/Dockerfile'
                    additionalBuildArgs '--build-arg TAG=3.8-buster'
                }
            }
            steps {
                sh '''
                ./testing/unittests/run3.sh
                '''
            }
            post {
                always {
                    junit 'testing/gw-unit-reports-3/*.xml'
                }
            }
        }

        stage('Start integration tests') {
            when {
                anyOf {
                    branch 'master'
                    branch 'develop'
                }
            }
            steps {
                script {
                    build(job: 'gateway-integration', wait: false, parameters: [
                        [$class: 'StringParameterValue', name: 'BRANCH_NAME', value: env.BRANCH_NAME],
                        [$class: 'StringParameterValue', name: 'GIT_COMMIT', value: env.GIT_COMMIT],
                    ])
                }
            }
        }
    }
}
