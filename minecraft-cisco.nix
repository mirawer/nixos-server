# Cisco's Fantasy Medieval RPG [Ultimate] V8Cr: new world, separate from Fabric.
# Official server file 7731292; client file 7731287; project 957753.
{ pkgs, lib, ... }:
let
  forgeServer = pkgs.writeShellApplication {
    name = "minecraft-server";
    text = ''
      test -s libraries/net/minecraftforge/forge/1.19.2-43.5.1/unix_args.txt
      exec ${pkgs.jdk17_headless}/bin/java "$@" \
        @libraries/net/minecraftforge/forge/1.19.2-43.5.1/unix_args.txt nogui
    '';
  };
in
{
  users.users.echo.extraGroups = [ "minecraft" ];
  services.minecraft-servers = {
    enable = true;
    eula = true;
    dataDir = "/srv/minecraft";
    openFirewall = false;
    managementSystem = {
      tmux.enable = false;
      systemd-socket.enable = true;
    };
    servers.cisco-v8cr = {
      enable = true;
      autoStart = true;
      package = forgeServer;
      # Pack defaults to 4G. Give it 5G without allocating all of this 8G host.
      # No AlwaysPreTouch: leave memory available until actually needed.
      jvmOpts = "-Xms2G -Xmx5G -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200";
      restart = "on-failure";
      serverProperties = {
        server-port = 25565;
        motd = "Echo - Cisco Ultimate V8Cr - Minecraft 1.19.2";
        level-name = "world";
        gamemode = "survival";
        difficulty = "normal";
        max-players = 8;
        view-distance = 16;
        simulation-distance = 6;
        online-mode = true;
        white-list = true;
        enforce-whitelist = true;
        enable-rcon = false;
        enable-query = false;
        enable-command-block = false;
      };
      # Preserve the game's existing accounts and operator levels, not Nix UUIDs.
      whitelist = { };
      operators = { };
      symlinks = {
        "kubejs/server_scripts/wladd.js" = ./wladd-cisco.js;
        "mods/spark-1.10.37-forge.jar" = pkgs.fetchurl {
          url = "https://cdn.modrinth.com/data/l6YH9Als/versions/1dlOe6ez/spark-1.10.37-forge.jar";
          sha512 = "0de99bfe5b31bed11ce2f7e173519a91542069a762b2d657bb1aa6517362255fcb8b8112ef28af5f4640db4d7e4b2618175830224c4d777e69489d8ac3c5d5db";
        };
      };
      extraStartPre = ''
        test -f .cisco-v8cr-payload-verified
        test -s libraries/net/minecraftforge/forge/1.19.2-43.5.1/unix_args.txt
        if find mods -maxdepth 1 -iname '*disabilit*' -print -quit | grep -q .; then
          echo "Refusing to start with Disabilities present" >&2
          exit 1
        fi
      '';
    };
  };
  networking.firewall.interfaces = {
    enp3s0.allowedTCPPorts = [ 25565 ];
    wg0.allowedTCPPorts = [ 25565 ];
  };
  systemd.services.minecraft-server-cisco-v8cr.serviceConfig = {
    MemoryHigh = "6G";
    MemoryMax = "6500M";
    MemorySwapMax = "1G";
    TimeoutStopSec = lib.mkForce "300s";
    ProtectSystem = "strict";
    ReadWritePaths = [ "/srv/minecraft/cisco-v8cr" ];
  };
}
