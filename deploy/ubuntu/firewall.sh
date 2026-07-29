#!/bin/sh
set -eu

command -v iptables >/dev/null 2>&1 || {
  echo "iptables compatibility layer is required by Docker on Ubuntu." >&2
  exit 1
}

iptables -N DOCKER-USER 2>/dev/null || true
iptables -N TUTORBOARD-DOCKER 2>/dev/null || true
iptables -F TUTORBOARD-DOCKER

iptables -A TUTORBOARD-DOCKER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A TUTORBOARD-DOCKER -i docker0 -j RETURN
iptables -A TUTORBOARD-DOCKER -i 'br+' -j RETURN
iptables -A TUTORBOARD-DOCKER -p tcp --dport 80 -j RETURN
iptables -A TUTORBOARD-DOCKER -p tcp --dport 443 -j RETURN
iptables -A TUTORBOARD-DOCKER -p udp --dport 443 -j RETURN
iptables -A TUTORBOARD-DOCKER -j DROP

while iptables -C DOCKER-USER -j TUTORBOARD-DOCKER 2>/dev/null; do
  iptables -D DOCKER-USER -j TUTORBOARD-DOCKER
done
iptables -I DOCKER-USER 1 -j TUTORBOARD-DOCKER

