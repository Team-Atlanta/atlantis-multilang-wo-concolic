{
  description = "Offline LSP runtime for atlantis-multilang-wo-concolic";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python3;
      py = python.pkgs;

      clangd = pkgs.fetchurl {
        url = "https://github.com/clangd/clangd/releases/download/20.1.0/clangd-linux-20.1.0.zip";
        hash = "sha256-sgYWr/sEze3wCK779qEVNqFg+lbdViqYH8tyhEe7QPg=";
      };
      clangdIndexer = pkgs.fetchurl {
        url = "https://github.com/clangd/clangd/releases/download/20.1.0/clangd_indexing_tools-linux-20.1.0.zip";
        hash = "sha256-3A/JLnQobwBOz4O07paZ7ZchnoYLu5UQIBr869RRhdo=";
      };
      gradle = pkgs.fetchurl {
        url = "https://services.gradle.org/distributions/gradle-7.3.3-bin.zip";
        hash = "sha256-tYbgSGiiL9gXyJcTMP7DfimPMkLrhcN0GBsS1jf4AwI=";
      };
      vscodeJava = pkgs.fetchurl {
        name = "vscode-java-linux-x64.vsix";
        url = "https://github.com/redhat-developer/vscode-java/releases/download/v1.23.0/java@linux-x64-1.23.0.vsix";
        hash = "sha256-1OqJguFFvCCeHvajXtIZ9aZr8wTdiLoduxmsK0Yio4A=";
      };
      intellicode = pkgs.fetchurl {
        url = "https://VisualStudioExptTeam.gallery.vsassets.io/_apis/public/gallery/publisher/VisualStudioExptTeam/extension/vscodeintellicode/1.2.30/assetbyname/Microsoft.VisualStudio.Services.VSIXPackage";
        hash = "sha256-f2Gn+W0QHN8jD5aCG+P93Y+JDr/vs2ldGL7uQwBK4lE=";
      };

      libCRS = py.buildPythonPackage {
        pname = "atlantis-libcrs";
        version = "0.1.0";
        src = ./libs/libCRS;
        format = "setuptools";
        propagatedBuildInputs = with py; [
          coloredlogs
          pyyaml
          gitpython
          requests
          tabulate
          gspread
          google-auth
          pandas
          opentelemetry-api
          opentelemetry-sdk
          opentelemetry-exporter-otlp-proto-grpc
          loguru
        ];
        doCheck = false;
      };

      multilspy = py.buildPythonPackage {
        pname = "multilspy";
        version = "0.0.14";
        src = ./libs/multilspy;
        format = "pyproject";
        nativeBuildInputs = [ py.flit-core pkgs.unzip ];
        postPatch = ''
          substituteInPlace pyproject.toml \
            --replace-fail 'jedi-language-server==0.46.0' 'jedi-language-server>=0.46.0' \
            --replace-fail 'requests==2.32.3' 'requests>=2.32.3'
        '';
        propagatedBuildInputs = with py; [
          pydantic
          jedi-language-server
          requests
          typing-extensions
          psutil
          loguru
        ];
        postInstall = ''
          clangd_dir="$out/${python.sitePackages}/multilspy/language_servers/clangd_language/clangd"
          mkdir -p "$clangd_dir"
          ${pkgs.unzip}/bin/unzip -oq ${clangd} -d "$clangd_dir"
          ${pkgs.unzip}/bin/unzip -oq ${clangdIndexer} -d "$clangd_dir"
          chmod +x "$clangd_dir/clangd_20.1.0/bin/clangd" \
            "$clangd_dir/clangd_20.1.0/bin/clangd-indexer"
        '';
        doCheck = false;
      };

      eclipseRuntime = pkgs.runCommand "atlantis-eclipse-jdtls-runtime" {
        nativeBuildInputs = [ pkgs.unzip ];
      } ''
        runtime="$out/share/atlantis-lsp/eclipse-jdtls"
        mkdir -p "$runtime/static" \
          "$runtime/static/vscode-java" \
          "$runtime/static/intellicode"
        unzip -q ${gradle} -d "$runtime/static"
        unzip -q ${vscodeJava} -d "$runtime/static/vscode-java"
        unzip -q ${intellicode} -d "$runtime/static/intellicode"
        rm -rf "$runtime/static/vscode-java/extension/jre/17.0.8.1-linux-x86_64"
        ln -s ${pkgs.jdk17_headless}/lib/openjdk \
          "$runtime/static/vscode-java/extension/jre/17.0.8.1-linux-x86_64"
      '';

      pythonEnv = python.withPackages (_: [ libCRS multilspy ]);
    in {
      packages.${system}.default = pkgs.buildEnv {
        name = "atlantis-multilang-wo-concolic-lsp-runtime";
        paths = [
          pythonEnv
          eclipseRuntime
          pkgs.git
          pkgs.pigz
          pkgs.socat
          pkgs.sqlite
          pkgs.util-linux
          pkgs.vim
        ];
      };
    };
}
