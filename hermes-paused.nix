# Temporary pause requested by echo on 2026-10-20.
# To resume, remove this module's import from flake.nix and rebuild.
# Keep users, packages, credentials and all state directories untouched.
{ lib, ... }:
{
  systemd.services.hermes-agent.enable = lib.mkForce false;
  systemd.services.hermes-mail-brief.enable = lib.mkForce false;
  systemd.services."hermes-mail-read@".enable = lib.mkForce false;
  systemd.sockets.hermes-mail-read.enable = lib.mkForce false;
  systemd.timers.hermes-mail-brief.enable = lib.mkForce false;
}
