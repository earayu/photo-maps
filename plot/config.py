import os
import toml

class Config:
    def __init__(self, config_file='config.toml'):
        self.config_file = config_file
        self.settings = self.load_config()

    def load_config(self):
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"配置文件 {self.config_file} 不存在。")
        with open(self.config_file, 'r', encoding='utf-8') as f:
            config = toml.load(f)
        return config.get('settings', {})