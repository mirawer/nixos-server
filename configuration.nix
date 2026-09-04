#	/\	/\
#	\/	\/	/\
#	(	 )	\/	Home Server
#	)	(	(		~M

{
  config,
  lib,
  pkgs,
  ...
}:

{
  imports = [
    ./hardware-configuration.nix
    ./vm-boot.nix
  ];

  networking.hostName = "server";
  time.timeZone = "Europe/Warsaw";

  i18n.defaultLocale = "en_US.UTF-8";
  console.keyMap = "pl";

  services.logind.settings.Login.HandleLidSwitch = "ignore";
  documentation.nixos.enable = false;

  environment.systemPackages = with pkgs; [
    vim
    wget
    git
  ];

  system.stateVersion = "26.05";

  #  --  --  Security  --  --

  users.mutableUsers = false;

  users.users.echo = {
    isNormalUser = true;
    extraGroups = [ "wheel" "media"];
    hashedPasswordFile = "/var/lib/secrets/echo.pass";
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJmN5fnFpjehHkQ2+iJhZvZy3gajCvex8iuFLdNP4ZX7 echo@server"
    ];
  };

  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      PermitRootLogin = "no";
      KbdInteractiveAuthentication = false;
      MaxAuthTries = 3;
    };
  };

  networking.firewall = {
    enable = true;
    allowedTCPPorts = [ 22 8096 ];
    allowedUDPPorts = [ 7359 ];
  };

  #  --  --  Optimalization  --  --

  networking.networkmanager.enable = false;

  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 50;
    priority = 100;
  };

  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 14d";
  };

  nix.optimise.automatic = true;

  boot.tmp.cleanOnBoot = true;

  services.journald.extraConfig = "SystemMaxUse=500M";

  #  --  --  Media  --  --

  users.groups.media = { };

  systemd.tmpfiles.rules = [
    #If it exists ten z if not then build with d	

    "d /srv/media 0755 root root - -"
    "d /srv/media/films 2750 echo media - -"
    "d /srv/media/series 2750 echo media - -"
    
    "z /srv/media 0755 root root - -"
    "z /srv/media/films 2750 echo media - -"
    "z /srv/media/series 2750 echo media - -"
  ];

  #  --  --  Jellyfin  --  --
  services.jellyfin.enable = true;
  
  users.users.jellyfin.extraGroups = [ "media" ];


}
