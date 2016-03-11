# A scalable MediaWiki Bundle

This bundle deploys Mediawiki, memcached, MySQL, and an haproxy. It requires a
minimum of 5 units. The mediawiki charm is placed behind the proxy so that you
can point DNS at the proxy and then scale the mediawiki unit up and down.

## Usage

All you should do after deployment is expose haproxy, you can do this via the
GUI or via the CLI:

    juju expose haproxy

To scale out mediawiki itself:

    juju add-unit mediawiki

There's no need to configure memcached, it is included in this bundle to use
mediawiki already. The MySQL database is set up in a master->slave
configuration so you can scale the database as well with:

    juju add-unit mysql-slave