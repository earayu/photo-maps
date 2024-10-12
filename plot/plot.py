import logging
import multiprocessing
from config import Config
from photo_meta_extractor import PhotoMetaExtractor
from mapper_plotter import MapperPlotter

if __name__ == "__main__":
    # 读取配置文件
    try:
        config = Config('config.toml').settings
    except Exception as e:
        logging.error(f"加载配置文件时出错: {e}")
        exit(1)

    # 创建 PhotoMetaExtractor 对象并处理照片
    extractor = PhotoMetaExtractor(
        photo_dir=config.get('source_directory'),
        output_dir=config.get('output_directory', "photo_data"),
        file_types=config.get('file_types', ['jpg', 'jpeg', 'png']),
        concurrency=config.get('concurrency', multiprocessing.cpu_count())
    )
    extractor.process_photos()

    # 持久化元数据到文件
    extractor.persist_metadata()

    # 创建 MapperPlotter 对象并绘制地图
    plotter = MapperPlotter(extractor.metadata_file, output_dir=config.get('output_directory', 'photo_map'))
    plotter.create_map()