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
    extraGroups = [ "wheel" ];
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
    allowedTCPPorts = [ 22 ];
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

}
