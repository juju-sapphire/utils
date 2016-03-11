
from datetime import datetime
import subprocess
import requests
import os
import sys
from urllib.parse import urlparse
from threading import Timer


def run(cmd, quiet=False, write_to=None, fail_ok=False, empty_return=False, timestamp=False, timeout=None, fail_exits=False):
    if not quiet:
        print(cmd)

    out = ""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, bufsize=1)
    lines_iterator = iter(p.stdout.readline, b"")

    if timeout:
        timer = Timer(timeout, p.kill)
        timer.start()

    try:
        for line in lines_iterator:
            line = line.decode()
            if not empty_return:
                out += line
            if timestamp:
                now = datetime.utcnow().isoformat(' ')
                line = now + '| ' + line
            if not quiet:
                sys.stdout.write(line)
            if write_to is not None:
                write_to.write(line)

        if not quiet:
            print('')

        p.poll()
    finally:
        if timeout:
            timer.cancel()

    if fail_ok:
        return out, p.returncode

    if p.returncode:
        if quiet:
            print('-' * 80)
            print(cmd, 'returned', p.returncode)
            print(out)
        else:
            print(cmd, 'returned', p.returncode)
        if fail_exits:
            exit(p.returncode)
        raise subprocess.CalledProcessError(p.returncode, cmd, out)

    return out


def sudo(command, **kwargs):
    return run('sudo ' + command, **kwargs)


def download(url, path):
    r = requests.get(url, stream=True)
    if r.status_code == 200:
        with open(path, 'wb') as f:
            for chunk in r.iter_content(1024):
                f.write(chunk)


def install_deb(url):
    filename = os.path.basename(urlparse(url).path)
    filename = os.path.join('/tmp/boblify/', filename)
    try:
        os.makedirs('/tmp/boblify/')
    except OSError:
        pass

    download(url, os.path.join('/tmp/boblify/', filename))
    sudo('gdebi -n ' + filename)


def install_ppas(ppas):
    sources = run('apt-cache policy', quiet=True)
    for ppa in ppas:
        path = 'ppa.launchpad.net/' + ppa.split(':')[1] + '/'
        if sources.find(path) == -1:
            sudo('add-apt-repository -y ' + ppa)


def install_packages(packages):
    packages_to_install = []
    for package in packages:
        rc = run('dpkg-query -s ' + package, fail_ok=True, quiet=True)[1]
        print(package, rc)
        if rc:
            packages_to_install.append(package)

    if len(packages_to_install):
        sudo('apt-get update')
        sudo('apt-get -y upgrade')
        sudo('apt-get -y install ' + ' '.join(packages_to_install))
