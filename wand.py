#!/usr/bin/python3

import subprocess
import yaml
import time
from datetime import datetime
from shelly import run


def wait_for_connection():
    while subprocess.call('timeout 5 juju status', shell=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT):
        time.sleep(5)


def bootstrapped():
    out, rc = run('timeout 1 juju status', fail_ok=True, quiet=True)
    return rc == 0


def status():
    wait_for_connection()
    return yaml.load(run('juju status --format yaml', quiet=True))


def juju(cmd, quiet=False, write_to=None, fail_ok=False, silent=False):
    if silent:
        quiet = True
    if not silent:
        print("juju cmd:", cmd)
    offline_cmds = [
        'destroy-environment',
        'switch',
        'bootstrap',
    ]
    offline = False
    for c in offline_cmds:
        if cmd.startswith(c):
            offline = True
            break
    if not (offline or fail_ok):
        wait_for_connection()

    return run("juju " + cmd, quiet, write_to, fail_ok)


def watch(store, key, value):
    if store.get(key) != value:
        print(datetime.now(), key + ":", value)
        store[key] = value


def wait(forever=False):
    keep_trying = True
    watching = {}

    while keep_trying or forever:

        time.sleep(5)
        try:
            s = status()
        except subprocess.CalledProcessError:
            continue
        keep_trying = False

        try:
            for name, m in s['machines'].items():
                agent_state = m.get('agent-state')
                watch(watching, name, agent_state)
                if agent_state != 'started':
                    keep_trying = True
                    continue

                ssms = m.get('state-server-member-status')
                if ssms and ssms != 'has-vote':
                    keep_trying = True
                    continue

                containers = m.get('containers')
                if containers:
                    for cname, c in containers.items():
                        watch(watching, cname, agent_state)
                        agent_state = c.get('agent-state')
                        if agent_state != 'started':
                            keep_trying = True

                if keep_trying:
                    continue

            for service_name, service in s['services'].items():
                if 'units' not in service:
                    continue
                for unit in list(service['units'].values()):
                    name = unit['machine'] + ' ' + service_name
                    unit_state = unit.get('agent-state', False) or unit.get('agent-status')['current']
                    watch(watching, name, unit_state)

                    name += ' workload-status'
                    if unit['workload-status'].get('message'):
                        watch(watching, name, unit['workload-status']['message'])
                    else:
                        watch(watching, name, '')

                    if unit_state not in ['started', 'idle']:
                        keep_trying = True
                        continue
        except KeyError as e:
            print(e)
            print("continuing...")


if __name__ == '__main__':
    start_at = 0
    if start_at <= 1:
        run('go install  -v github.com/juju/juju/...')
        juju('destroy-environment --force amzeu', fail_ok=True)
        juju('switch amzeu')
        juju('bootstrap --upload-tools')
        # juju('set-env logging-config=juju.state.presence=TRACE')
        juju(r'set-env logging-config=\<root\>=TRACE')
        wait()

    if start_at <= 2:
        # I don't know why, but deploying a charm before doing ensure-availability
        # seems to help us not get stuck in the waiting for has-vote state.
        juju('deploy ubuntu')
        wait()

    if start_at <= 3:
        juju('ensure-availability -n 3')
        wait()

    if start_at <= 4:
        # Need to wait until the Mongo servers actually do their HA thing. This
        # is not the same as status showing everything as started. Bother.
        #time.sleep(30)
        # 30 seconds seems to be more than enough time to let things settle.
        while True:
            try:
                juju('ssh 0 "sudo halt -p"')
                break
            except subprocess.CalledProcessError:
                time.sleep(5)

        time.sleep(60)
        juju('ensure-availability -n 3')
