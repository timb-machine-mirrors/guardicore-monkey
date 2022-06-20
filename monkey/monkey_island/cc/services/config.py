import collections
import copy
import functools
import logging
import re
from itertools import chain
from typing import Any, Dict, List

from jsonschema import Draft4Validator, validators

from common.config_value_paths import (
    LM_HASH_LIST_PATH,
    NTLM_HASH_LIST_PATH,
    PASSWORD_LIST_PATH,
    PBA_LINUX_FILENAME_PATH,
    PBA_WINDOWS_FILENAME_PATH,
    SSH_KEYS_PATH,
    USER_LIST_PATH,
)
from monkey_island.cc.database import mongo
from monkey_island.cc.server_utils.encryption import (
    SensitiveField,
    StringEncryptor,
    decrypt_dict,
    encrypt_dict,
    get_datastore_encryptor,
)
from monkey_island.cc.services.config_manipulator import update_config_per_mode
from monkey_island.cc.services.config_schema.config_schema import SCHEMA
from monkey_island.cc.services.mode.island_mode_service import ModeNotSetError, get_mode
from monkey_island.cc.services.post_breach_files import PostBreachFilesService

logger = logging.getLogger(__name__)

# This should be used for config values of array type (array of strings only)
ENCRYPTED_CONFIG_VALUES = [
    PASSWORD_LIST_PATH,
    LM_HASH_LIST_PATH,
    NTLM_HASH_LIST_PATH,
    SSH_KEYS_PATH,
]

SENSITIVE_SSH_KEY_FIELDS = [
    SensitiveField(path="private_key", field_encryptor=StringEncryptor),
    SensitiveField(path="public_key", field_encryptor=StringEncryptor),
]

SMB_DOWNLOAD_TIMEOUT = 30


class ConfigService:
    default_config = None

    def __init__(self):
        pass

    @staticmethod
    def get_config(should_decrypt=True, is_island=False):
        """
        Gets the entire global config.

        :param should_decrypt: If True, all config values which are set as encrypted will be \
         decrypted. \
        :param is_island: If True, will include island specific configuration parameters. \
        :return: The entire global config.
        """

        # is_initial_config and should_decrypt are only there to compare if we are on the
        # default configuration or did user modified it already
        config = mongo.db.config.find_one() or {}
        config.pop("_id", None)
        if should_decrypt and len(config) > 0:
            ConfigService.decrypt_config(config)
        if not is_island:
            config.get("cnc", {}).pop("aws_config", None)
        return config

    @staticmethod
    def get_config_value(config_key_as_arr, should_decrypt=True):
        """
        Get a specific config value.

        :param config_key_as_arr: The config key as an array.
         e.g. ['basic', 'credentials','exploit_password_list'].
        :param should_decrypt: If True, the value of the config key will be decrypted
                               (if it's in the list of encrypted config values).
        :return: The value of the requested config key.
        """
        config_key = functools.reduce(lambda x, y: x + "." + y, config_key_as_arr)

        # This should just call get_config from repository. If None, then call get_default prob
        config = mongo.db.config.find_one({}, {config_key: 1})

        for config_key_part in config_key_as_arr:
            config = config[config_key_part]
        if should_decrypt:
            if config_key_as_arr in ENCRYPTED_CONFIG_VALUES:
                if isinstance(config, str):
                    config = get_datastore_encryptor().decrypt(config)
                elif isinstance(config, list):
                    if config:
                        if isinstance(config[0], str):
                            config = [get_datastore_encryptor().decrypt(x) for x in config]
                        elif isinstance(config[0], dict) and "public_key" in config[0]:
                            config = [decrypt_dict(SENSITIVE_SSH_KEY_FIELDS, x) for x in config]

        return config

    @staticmethod
    def set_config_value(config_key_as_arr, value):
        mongo_key = ".".join(config_key_as_arr)
        mongo.db.config.update({}, {"$set": {mongo_key: value}})

    @staticmethod
    def get_flat_config(should_decrypt=True):
        config_json = ConfigService.get_config(should_decrypt)
        flat_config_json = {}
        for i in config_json:
            if i == "ransomware":
                # Don't flatten the ransomware because ransomware payload expects a dictionary #1260
                flat_config_json[i] = config_json[i]
                continue
            for j in config_json[i]:
                for k in config_json[i][j]:
                    if isinstance(config_json[i][j][k], dict):
                        for key, value in config_json[i][j][k].items():
                            flat_config_json[key] = value
                    else:
                        flat_config_json[k] = config_json[i][j][k]

        return flat_config_json

    @staticmethod
    def get_config_schema():
        return SCHEMA

    # Not added to interface because it's doable by get_config_field + set_config_field
    @staticmethod
    def add_item_to_config_set_if_dont_exist(item_path_array, item_value, should_encrypt):
        item_key = ".".join(item_path_array)
        items_from_config = ConfigService.get_config_value(item_path_array, should_encrypt)
        if item_value in items_from_config:
            return
        if should_encrypt:
            if isinstance(item_value, dict):
                item_value = encrypt_dict(SENSITIVE_SSH_KEY_FIELDS, item_value)
            else:
                item_value = get_datastore_encryptor().encrypt(item_value)
        mongo.db.config.update({}, {"$addToSet": {item_key: item_value}})

    @staticmethod
    def creds_add_username(username):
        ConfigService.add_item_to_config_set_if_dont_exist(
            USER_LIST_PATH, username, should_encrypt=False
        )

    @staticmethod
    def creds_add_password(password):
        ConfigService.add_item_to_config_set_if_dont_exist(
            PASSWORD_LIST_PATH, password, should_encrypt=True
        )

    @staticmethod
    def creds_add_lm_hash(lm_hash):
        ConfigService.add_item_to_config_set_if_dont_exist(
            LM_HASH_LIST_PATH, lm_hash, should_encrypt=True
        )

    @staticmethod
    def creds_add_ntlm_hash(ntlm_hash):
        ConfigService.add_item_to_config_set_if_dont_exist(
            NTLM_HASH_LIST_PATH, ntlm_hash, should_encrypt=True
        )

    @staticmethod
    def ssh_add_keys(public_key, private_key):
        ConfigService.add_item_to_config_set_if_dont_exist(
            SSH_KEYS_PATH,
            {"public_key": public_key, "private_key": private_key},
            should_encrypt=True,
        )

    def _filter_none_values(data):
        if isinstance(data, dict):
            return {
                k: ConfigService._filter_none_values(v)
                for k, v in data.items()
                if k is not None and v is not None
            }
        elif isinstance(data, list):
            return [ConfigService._filter_none_values(item) for item in data if item is not None]
        else:
            return data

    @staticmethod
    def update_config(config_json, should_encrypt):
        # PBA file upload happens on pba_file_upload endpoint and corresponding config options
        # are set there
        config_json = ConfigService._filter_none_values(config_json)
        ConfigService.set_config_PBA_files(config_json)
        if should_encrypt:
            try:
                ConfigService.encrypt_config(config_json)
            except KeyError:
                logger.error("Bad configuration file was submitted.")
                return False
        mongo.db.config.update({}, {"$set": config_json}, upsert=True)
        logger.info("monkey config was updated")
        return True

    @staticmethod
    def set_config_PBA_files(config_json):
        """
        Sets PBA file info in config_json to current config's PBA file info values.
        :param config_json: config_json that will be modified
        """
        if ConfigService.get_config():
            linux_filename = ConfigService.get_config_value(PBA_LINUX_FILENAME_PATH)
            windows_filename = ConfigService.get_config_value(PBA_WINDOWS_FILENAME_PATH)

            ConfigService.set_config_value(PBA_LINUX_FILENAME_PATH, linux_filename)
            ConfigService.set_config_value(PBA_WINDOWS_FILENAME_PATH, windows_filename)

    @staticmethod
    def init_default_config():
        if ConfigService.default_config is None:
            default_validating_draft4_validator = ConfigService._extend_config_with_default(
                Draft4Validator
            )
            config = {}
            default_validating_draft4_validator(SCHEMA).validate(config)
            ConfigService.default_config = config

    @staticmethod
    def get_default_config(should_encrypt=False):
        ConfigService.init_default_config()
        config = copy.deepcopy(ConfigService.default_config)

        if should_encrypt:
            ConfigService.encrypt_config(config)

        logger.info("Default config was called")

        return config

    @staticmethod
    def init_config():
        if ConfigService.get_config(should_decrypt=False) != {}:
            return
        ConfigService.reset_config()

    @staticmethod
    def reset_config():
        PostBreachFilesService.remove_PBA_files()
        config = ConfigService.get_default_config(True)
        try:
            mode = get_mode()
            update_config_per_mode(mode, config, should_encrypt=False)
        except ModeNotSetError:
            ConfigService.update_config(config, should_encrypt=False)
        logger.info("Monkey config reset was called")

    @staticmethod
    def _extend_config_with_default(validator_class):
        validate_properties = validator_class.VALIDATORS["properties"]

        def set_defaults(validator, properties, instance, schema):
            # Do it only for root.
            if instance != {}:
                return
            for property1, subschema1 in list(properties.items()):
                main_dict = {}
                for property2, subschema2 in list(subschema1["properties"].items()):
                    sub_dict = {}
                    for property3, subschema3 in list(subschema2["properties"].items()):
                        if "default" in subschema3:
                            sub_dict[property3] = subschema3["default"]
                        elif "properties" in subschema3:
                            layer_3_dict = {}
                            for property4, subschema4 in list(subschema3["properties"].items()):
                                if "properties" in subschema4:
                                    raise ValueError(
                                        "monkey/monkey_island/cc/services/config.py "
                                        "can't handle 5 level config. "
                                        "Either change back the config or refactor."
                                    )
                                if "default" in subschema4:
                                    layer_3_dict[property4] = subschema4["default"]
                            sub_dict[property3] = layer_3_dict
                    main_dict[property2] = sub_dict
                instance.setdefault(property1, main_dict)

            for error in validate_properties(validator, properties, instance, schema):
                yield error

        return validators.extend(
            validator_class,
            {"properties": set_defaults},
        )

    @staticmethod
    def decrypt_config(config):
        ConfigService._encrypt_or_decrypt_config(config, True)

    @staticmethod
    def encrypt_config(config):
        ConfigService._encrypt_or_decrypt_config(config, False)

    @staticmethod
    def _encrypt_or_decrypt_config(config, is_decrypt=False):
        for config_arr_as_array in ENCRYPTED_CONFIG_VALUES:
            config_arr = config
            parent_config_arr = None

            # Because the config isn't flat, this for-loop gets the actual config value out of
            # the config
            for config_key_part in config_arr_as_array:
                parent_config_arr = config_arr
                config_arr = config_arr[config_key_part]

            if isinstance(config_arr, collections.abc.Sequence) and not isinstance(config_arr, str):
                for i in range(len(config_arr)):
                    # Check if array of shh key pairs and then decrypt
                    if isinstance(config_arr[i], dict) and "public_key" in config_arr[i]:
                        config_arr[i] = (
                            decrypt_dict(SENSITIVE_SSH_KEY_FIELDS, config_arr[i])
                            if is_decrypt
                            else encrypt_dict(SENSITIVE_SSH_KEY_FIELDS, config_arr[i])
                        )
                    else:
                        config_arr[i] = (
                            get_datastore_encryptor().decrypt(config_arr[i])
                            if is_decrypt
                            else get_datastore_encryptor().encrypt(config_arr[i])
                        )
            else:
                parent_config_arr[config_arr_as_array[-1]] = (
                    get_datastore_encryptor().decrypt(config_arr)
                    if is_decrypt
                    else get_datastore_encryptor().encrypt(config_arr)
                )

    @staticmethod
    def get_config_propagation_credentials_from_flat_config(config) -> Dict[str, List[str]]:
        return {
            "exploit_user_list": config.get("exploit_user_list", []),
            "exploit_password_list": config.get("exploit_password_list", []),
            "exploit_lm_hash_list": config.get("exploit_lm_hash_list", []),
            "exploit_ntlm_hash_list": config.get("exploit_ntlm_hash_list", []),
            "exploit_ssh_keys": config.get("exploit_ssh_keys", []),
        }

    @staticmethod
    def format_flat_config_for_agent():
        config = ConfigService.get_flat_config()
        ConfigService._remove_credentials_from_flat_config(config)
        ConfigService._format_payloads_from_flat_config(config)
        ConfigService._format_pbas_from_flat_config(config)
        ConfigService._format_propagation_from_flat_config(config)
        ConfigService._format_credential_collectors(config)

        # Ok, I'll admit this is just sort of jammed in here. But this code is going away very soon.
        del config["HTTP_PORTS"]

        return config

    @staticmethod
    def _remove_credentials_from_flat_config(config: Dict):
        fields_to_remove = {
            "exploit_lm_hash_list",
            "exploit_ntlm_hash_list",
            "exploit_password_list",
            "exploit_ssh_keys",
            "exploit_user_list",
        }

        for field in fields_to_remove:
            config.pop(field, None)

    @staticmethod
    def _format_credential_collectors(config: Dict):
        collectors = [
            {"name": collector, "options": {}} for collector in config["credential_collectors"]
        ]
        config["credential_collectors"] = collectors

    @staticmethod
    def _format_payloads_from_flat_config(config: Dict):
        config.setdefault("payloads", []).append(
            {"name": "ransomware", "options": config["ransomware"]}
        )
        config.pop("ransomware", None)

    @staticmethod
    def _format_pbas_from_flat_config(config: Dict):
        flat_linux_command_field = "custom_PBA_linux_cmd"
        flat_linux_filename_field = "PBA_linux_filename"
        flat_windows_command_field = "custom_PBA_windows_cmd"
        flat_windows_filename_field = "PBA_windows_filename"

        formatted_pbas_config = [
            {"name": pba, "options": {}} for pba in config.get("post_breach_actions", [])
        ]

        config["custom_pbas"] = {
            "linux_command": config.get(flat_linux_command_field, ""),
            "linux_filename": config.get(flat_linux_filename_field, ""),
            "windows_command": config.get(flat_windows_command_field, ""),
            "windows_filename": config.get(flat_windows_filename_field, ""),
        }

        config["post_breach_actions"] = formatted_pbas_config

        config.pop(flat_linux_command_field, None)
        config.pop(flat_linux_filename_field, None)
        config.pop(flat_windows_command_field, None)
        config.pop(flat_windows_filename_field, None)

    @staticmethod
    def _format_propagation_from_flat_config(config: Dict):
        formatted_propagation_config = {"network_scan": {}, "maximum_depth": {}, "exploitation": {}}

        formatted_propagation_config[
            "network_scan"
        ] = ConfigService._format_network_scan_from_flat_config(config)

        formatted_propagation_config[
            "exploitation"
        ] = ConfigService._format_exploiters_from_flat_config(config)

        formatted_propagation_config["maximum_depth"] = config["depth"]
        del config["depth"]

        config["propagation"] = formatted_propagation_config

    @staticmethod
    def _format_network_scan_from_flat_config(config: Dict) -> Dict[str, Any]:
        formatted_network_scan_config = {"tcp": {}, "icmp": {}, "fingerprinters": [], "targets": {}}

        formatted_network_scan_config["tcp"] = ConfigService._format_tcp_scan_from_flat_config(
            config
        )
        formatted_network_scan_config["icmp"] = ConfigService._format_icmp_scan_from_flat_config(
            config
        )
        formatted_network_scan_config[
            "fingerprinters"
        ] = ConfigService._format_fingerprinters_from_flat_config(config)

        formatted_network_scan_config["targets"] = ConfigService._format_targets_from_flat_config(
            config
        )

        return formatted_network_scan_config

    @staticmethod
    def _format_tcp_scan_from_flat_config(config: Dict) -> Dict[str, Any]:
        flat_http_ports_field = "HTTP_PORTS"
        flat_tcp_timeout_field = "tcp_scan_timeout"
        flat_tcp_ports_field = "tcp_target_ports"

        formatted_tcp_scan_config = {}

        formatted_tcp_scan_config["timeout"] = config[flat_tcp_timeout_field]

        ports = ConfigService._union_tcp_and_http_ports(
            config[flat_tcp_ports_field], config[flat_http_ports_field]
        )
        formatted_tcp_scan_config["ports"] = ports

        # Do not remove HTTP_PORTS field. Other components besides scanning need it.
        config.pop(flat_tcp_timeout_field, None)
        config.pop(flat_tcp_ports_field, None)

        return formatted_tcp_scan_config

    @staticmethod
    def _union_tcp_and_http_ports(tcp_ports: List[int], http_ports: List[int]) -> List[int]:
        combined_ports = list(set(tcp_ports) | set(http_ports))

        return sorted(combined_ports)

    @staticmethod
    def _format_icmp_scan_from_flat_config(config: Dict) -> Dict[str, Any]:
        flat_ping_timeout_field = "ping_scan_timeout"

        formatted_icmp_scan_config = {}
        formatted_icmp_scan_config["timeout"] = config[flat_ping_timeout_field]

        config.pop(flat_ping_timeout_field, None)

        return formatted_icmp_scan_config

    @staticmethod
    def _format_fingerprinters_from_flat_config(config: Dict) -> List[Dict[str, Any]]:
        flat_fingerprinter_classes_field = "finger_classes"
        flat_http_ports_field = "HTTP_PORTS"

        formatted_fingerprinters = [
            {"name": f, "options": {}} for f in sorted(config[flat_fingerprinter_classes_field])
        ]

        for fp in formatted_fingerprinters:
            if fp["name"] == "HTTPFinger":
                fp["options"] = {"http_ports": sorted(config[flat_http_ports_field])}

            fp["name"] = ConfigService._translate_fingerprinter_name(fp["name"])

        config.pop(flat_fingerprinter_classes_field)
        return formatted_fingerprinters

    @staticmethod
    def _translate_fingerprinter_name(name: str) -> str:
        # This translates names like "HTTPFinger" to "http". "HTTPFinger" is an old classname on the
        # agent-side and is therefore unnecessarily couples the island to the fingerprinter's
        # implementation within the agent. For the time being, fingerprinters will have names like
        # "http", "ssh", "elastic", etc. This will be revisited when fingerprinters become plugins.
        return re.sub(r"Finger", "", name).lower()

    @staticmethod
    def _format_targets_from_flat_config(config: Dict) -> Dict[str, Any]:
        flat_blocked_ips_field = "blocked_ips"
        flat_inaccessible_subnets_field = "inaccessible_subnets"
        flat_local_network_scan_field = "local_network_scan"
        flat_subnet_scan_list_field = "subnet_scan_list"

        formatted_scan_targets_config = {}

        formatted_scan_targets_config[flat_blocked_ips_field] = config[flat_blocked_ips_field]
        formatted_scan_targets_config[flat_inaccessible_subnets_field] = config[
            flat_inaccessible_subnets_field
        ]
        formatted_scan_targets_config[flat_local_network_scan_field] = config[
            flat_local_network_scan_field
        ]
        formatted_scan_targets_config["subnets"] = config[
            flat_subnet_scan_list_field
        ]

        config.pop(flat_blocked_ips_field, None)
        config.pop(flat_inaccessible_subnets_field, None)
        config.pop(flat_local_network_scan_field, None)
        config.pop(flat_subnet_scan_list_field, None)

        return formatted_scan_targets_config

    @staticmethod
    def _format_exploiters_from_flat_config(config: Dict) -> Dict[str, List[Dict[str, Any]]]:
        flat_config_exploiter_classes_field = "exploiter_classes"
        brute_force_category = "brute_force"
        vulnerability_category = "vulnerability"
        brute_force_exploiters = {
            "MSSQLExploiter",
            "PowerShellExploiter",
            "SSHExploiter",
            "SmbExploiter",
            "WmiExploiter",
        }

        exploit_options = {}

        exploit_options["http_ports"] = sorted(config["HTTP_PORTS"])

        formatted_exploiters_config = {
            "options": exploit_options,
            "brute_force": [],
            "vulnerability": [],
        }

        for exploiter in sorted(config[flat_config_exploiter_classes_field]):
            category = (
                brute_force_category
                if exploiter in brute_force_exploiters
                else vulnerability_category
            )

            formatted_exploiters_config[category].append({"name": exploiter, "options": {}})

        config.pop(flat_config_exploiter_classes_field, None)

        formatted_exploiters_config = ConfigService._add_smb_download_timeout_to_exploiters(
            formatted_exploiters_config
        )
        return ConfigService._add_supported_os_to_exploiters(formatted_exploiters_config)

    @staticmethod
    def _add_smb_download_timeout_to_exploiters(
        formatted_config: Dict,
    ) -> Dict[str, List[Dict[str, Any]]]:
        new_config = copy.deepcopy(formatted_config)
        uses_smb_timeout = {"SmbExploiter", "WmiExploiter"}

        for exploiter in filter(lambda e: e["name"] in uses_smb_timeout, new_config["brute_force"]):
            exploiter["options"]["smb_download_timeout"] = SMB_DOWNLOAD_TIMEOUT

        return new_config

    @staticmethod
    def _add_supported_os_to_exploiters(
        formatted_config: Dict,
    ) -> Dict[str, List[Dict[str, Any]]]:
        supported_os = {
            "HadoopExploiter": ["linux", "windows"],
            "Log4ShellExploiter": ["linux", "windows"],
            "MSSQLExploiter": ["windows"],
            "PowerShellExploiter": ["windows"],
            "SSHExploiter": ["linux"],
            "SmbExploiter": ["windows"],
            "WmiExploiter": ["windows"],
            "ZerologonExploiter": ["windows"],
        }
        new_config = copy.deepcopy(formatted_config)
        for exploiter in chain(new_config["brute_force"], new_config["vulnerability"]):
            exploiter["supported_os"] = supported_os.get(exploiter["name"], [])

        return new_config
