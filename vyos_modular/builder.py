import abc
import dataclasses
import pathlib
import shutil
import typing as t

import vyos_modular.commands
from vyos_modular.model import ModuleConfig, Repositories, load_module_config

VYOS_CORE_GIT = "https://github.com/vyos/vyos-1x.git"
VYOS_BUILD_GIT = "https://github.com/vyos/vyos-build.git"

VENDOR_DIR = pathlib.Path("vendor")
VENDOR_DIR.mkdir(exist_ok=True)


@dataclasses.dataclass
class VyosModule:
    name: str
    path: pathlib.Path
    config: ModuleConfig


class Builder(abc.ABC):
    def __init__(self, config):
        super().__init__()

        self.should_build_core = False
        self.config = config
        self.build_dir = pathlib.Path("build")
        self.bin_dir = pathlib.Path("bin")
        self.modules: t.Collection[VyosModule] = []

        self.vyos_core_name = "vyos-core"
        self.vyos_build_name = "vyos-build"

        for folder in [self.build_dir, self.bin_dir]:
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir()
        print(f"DEBUG: {self.build_dir}")

    @abc.abstractmethod
    def _build_iso(self):
        raise NotImplementedError()

    def _clone_vyos(self):
        # Clone repos and copy structure to working dir
        vendor_vyos_core_path = VENDOR_DIR / self.vyos_core_name
        vendor_vyos_build_path = VENDOR_DIR / self.vyos_build_name

        print("INFO: Cloning vyos-core")
        vyos_modular.commands.clone_repo(
            VYOS_CORE_GIT, self.config["vyos_branch"], vendor_vyos_core_path
        )
        shutil.copytree(
            vendor_vyos_core_path, self.build_dir / self.vyos_core_name, symlinks=True
        )

        print("INFO: Cloning vyos-build")
        vyos_modular.commands.clone_repo(
            VYOS_BUILD_GIT, self.config["vyos_branch"], vendor_vyos_build_path
        )
        shutil.copytree(
            vendor_vyos_build_path, self.build_dir / self.vyos_build_name, symlinks=True
        )

    def _clone_modules(self):
        for module in self.config["modules"]:
            # Clone / Copy module to vendor folder
            module_dest = None
            if module["type"] == "local":
                path = pathlib.Path(module["path"])
                module_dest = VENDOR_DIR / path.name
                print(f"INFO: Installing local module {path.name}")
                if module_dest.is_dir():
                    print(f"WARN: Module {path.name} already installed")
                else:
                    shutil.copytree(path, module_dest)
            elif module["type"] == "git":
                module_dest = VENDOR_DIR / pathlib.Path(module["url"]).stem
                print(f"INFO: Installing remote module {module_dest.name}")
                if module_dest.is_dir():
                    print(f"WARN: Module {module_dest.name} already installed")
                else:
                    vyos_modular.commands.clone_repo(
                        module["url"], module["version"], module_dest
                    )
            else:
                raise RuntimeError(f"Unsupported module type {module['type']}")

            # Process module.yaml file

            module_config = load_module_config(module_dest)

            self.modules.append(
                VyosModule(
                    name=module_config.name, path=module_dest, config=module_config
                )
            )

    def _apply_modules(self):
        for module in self.modules:
            print(f"INFO: Applying module {module.name}")
            vyos_core_overlay_path = module.path / self.vyos_core_name / "overlay"
            if vyos_core_overlay_path.is_dir():
                self.should_build_core = True
                print(f"INFO: Applying vyos-core overlay from {module.name}")
                overlay_destination_path = self.build_dir / self.vyos_core_name
                vyos_modular.commands.apply_overlay(
                    vyos_core_overlay_path, overlay_destination_path
                )
            vyos_core_patch_path = module.path / self.vyos_core_name / "patches"
            if vyos_core_patch_path.is_dir():
                self.should_build_core = True
                for patch in vyos_core_patch_path.iterdir():
                    print(
                        f"INFO: Applying patch {patch.name} from {module.name} to vyos-core"
                    )
                    vyos_modular.commands.apply_patch(
                        patch, self.build_dir / self.vyos_core_name
                    )

            vyos_build_overlay_path = module.path / self.vyos_build_name / "overlay"
            if vyos_build_overlay_path.is_dir():
                print(f"INFO: Applying vyos-build overlay from {module.name}")
                overlay_destination_path = self.build_dir / self.vyos_build_name
                vyos_modular.commands.apply_overlay(
                    vyos_build_overlay_path, overlay_destination_path
                )
            vyos_build_patch_path = module.path / self.vyos_build_name / "patches"
            if vyos_build_patch_path.is_dir():
                for patch in vyos_build_patch_path.iterdir():
                    print(
                        f"INFO: Applying patch {patch.name} from {module.name} to vyos-build"
                    )
                    vyos_modular.commands.apply_patch(
                        patch, self.build_dir / self.vyos_build_name
                    )

            if module.config.package_urls:
                for package_url in module.config.package_urls:
                    packages_dir = self.build_dir / self.vyos_build_name / "packages"
                    vyos_modular.commands.download_package(packages_dir, package_url)

            if module.config.vyos_core_script:
                vyos_modular.commands.run_script(
                    self.build_dir, self.vyos_core_name, module.config.vyos_core_script
                )

            if module.config.vyos_build_script:
                vyos_modular.commands.run_script(
                    self.build_dir,
                    self.vyos_build_name,
                    module.config.vyos_build_script,
                )

    def _build_core(self):
        # Build vyos-core
        vyos_modular.commands.run_vyos_core_cmd(
            ["dpkg-buildpackage", "-uc", "-us", "-tc", "-b"],
            vyos_core_dir=self.build_dir / self.vyos_core_name,
            vyos_branch=self.config["vyos_branch"],
        )

        # Copy artifacts
        for deb in self.build_dir.glob("*.deb"):
            shutil.copy(deb, self.bin_dir)

        # Specifically copy the vyos-core deb to be built into the ISO
        iso_deb = next(self.build_dir.glob("vyos-1x_*.deb"))
        shutil.copy(iso_deb, self.build_dir / self.vyos_build_name / "packages")

    def run(self):
        self._clone_vyos()
        self._clone_modules()
        self._apply_modules()
        if self.should_build_core:
            self._build_core()
        self._build_iso()

    def build_core(self):
        pass


class EquuleusBuilder(Builder):
    def _build_iso(self):
        configure_cmd = [
            "./configure",
            "--architecture",
            "amd64",
            "--build-type",
            "production",
            "--version",
            f"{self.config['name']}-{self.config['vyos_branch']}",
        ]

        if "build_comment" in self.config:
            configure_cmd += ["--build-comment", self.config["build_comment"]]

        # Build list of additional repositories and packages from modules
        for module in self.modules:
            if module.config.repositories:
                for repository in module.config.repositories:
                    configure_cmd += [
                        "--custom-apt-entry",
                        repository.apt_entry,
                        "--custom-apt-key",
                        repository.gpg_key,
                    ]
            if module.config.packages:
                for package in module.config.packages:
                    configure_cmd += ["--custom-package", package]

        vyos_modular.commands.run_vyos_build_cmd(
            configure_cmd,
            self.build_dir / self.vyos_build_name,
            self.config["vyos_branch"],
        )

        vyos_modular.commands.run_vyos_build_cmd(
            ["sudo", "make", "iso"],
            self.build_dir / self.vyos_build_name,
            self.config["vyos_branch"],
        )

        build_output = self.build_dir / self.vyos_build_name / "build"
        iso = next(build_output.glob("vyos-*.iso"))
        shutil.copy(iso, self.bin_dir)


class SaggitaBuilder(Builder):
    def _build_iso(self):
        # Carry out iso build using Saggita vyos build branch
        configure_cmd = [
            "sudo",
            "./build-vyos-image",
            "--architecture",
            "amd64",
            "--build-type",
            "release",
            "--version",
            f"{self.config['name']}-{self.config['vyos_branch']}",
        ]

        if "build_comment" in self.config:
            configure_cmd += ["--build-comment", self.config["build_comment"]]

        # Build list of additional repositories and packages from modules
        for module in self.modules:
            if module.config.repositories:
                for repository in module.config.repositories:
                    configure_cmd += [
                        "--custom-apt-entry",
                        repository.apt_entry,
                        "--custom-apt-key",
                        "/vyos/" + repository.gpg_key,
                    ]
            if module.config.packages:
                for package in module.config.packages:
                    configure_cmd += ["--custom-package", package]

        configure_cmd += ["iso"]

        vyos_modular.commands.run_vyos_build_cmd(
            configure_cmd,
            self.build_dir / self.vyos_build_name,
            self.config["vyos_branch"],
        )

        build_output = self.build_dir / self.vyos_build_name / "build"
        iso = next(build_output.glob("vyos-*.iso"))
        shutil.copy(iso, self.bin_dir)

class CircinusBuilder(Builder):
    def _build_iso(self):
        # Carry out iso build using Circinus vyos build branch
        configure_cmd = [
            "sudo",
            "./build-vyos-image",
            "--architecture",
            "amd64",
            "--build-type",
            "release",
            "--version",
            f"{self.config['name']}-{self.config['vyos_branch']}",
        ]

        if "build_comment" in self.config:
            configure_cmd += ["--build-comment", self.config["build_comment"]]

        # Build list of additional repositories and packages from modules
        for module in self.modules:
            if module.config.repositories:
                for repository in module.config.repositories:
                    configure_cmd += [
                        "--custom-apt-entry",
                        repository.apt_entry,
                        "--custom-apt-key",
                        "/vyos/" + repository.gpg_key,
                    ]
            if module.config.packages:
                for package in module.config.packages:
                    configure_cmd += ["--custom-package", package]

        configure_cmd += ["iso"]

        vyos_modular.commands.run_vyos_build_cmd(
            configure_cmd,
            self.build_dir / self.vyos_build_name,
            self.config["vyos_branch"],
        )

        build_output = self.build_dir / self.vyos_build_name / "build"
        iso = next(build_output.glob("vyos-*.iso"))
        shutil.copy(iso, self.bin_dir)