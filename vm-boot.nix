{ ... }:

{
  boot.loader.grub.enable = true;
  boot.loader.grub.device = "/dev/vda";

  boot.kernelParams = [ "console=ttyS0,115200n8" ];

  boot.loader.grub.extraConfig = ''
    	serial --speed=115200
    	terminal_output serial
     	terminal_input serial
  '';
}
