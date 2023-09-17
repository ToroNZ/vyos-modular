import argparse
import pathlib

import yaml

import vyos_modular.builder

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Custom Vyos image builder")
    parser.add_argument("--config", "-c", type=pathlib.Path, required=True)
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as config_fh:
        config = yaml.load(config_fh, Loader=yaml.SafeLoader)

    match config["vyos_branch"]:
        case "equuleus" | "1.3":
            config["vyos_branch"] = "equuleus"
            builder = vyos_modular.builder.EquuleusBuilder(config)
        case "sagitta" | "1.4":
            config["vyos_branch"] = "sagitta"
            builder = vyos_modular.builder.SaggitaBuilder(config)
        case "current" | "circinus" | "1.5":
            config["vyos_branch"] = "current"
            builder = vyos_modular.builder.CircinusBuilder(config)
        case other:
            raise ValueError(f"Unsupported build branch {config['vyos_branch']}")

    builder.run()
