import hashlib
import json
import logging
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from tqdm import tqdm

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class PhotoMetaExtractor:
    def __init__(self, photo_dir, output_dir="photo_data", file_types=None, concurrency=None):
        self.photo_dir = photo_dir
        self.output_dir = output_dir
        self.file_types = file_types if file_types else ['jpg', 'jpeg', 'png']
        self.concurrency = concurrency if concurrency else multiprocessing.cpu_count()

        self.photos_data = []
        self.thumbnail_dir = os.path.join(self.output_dir, "thumbnails")
        self.metadata_file = os.path.join(self.output_dir, "photos_metadata.json")
        self.existing_md5 = set()

        # 创建输出目录
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.thumbnail_dir, exist_ok=True)

        # 加载已有的元数据
        self.load_existing_metadata()

    def load_existing_metadata(self):
        """加载已有的元数据，避免重复处理相同的文件"""
        if os.path.exists(self.metadata_file):
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                    self.existing_md5 = {item['md5'] for item in existing_data}
                    self.photos_data = existing_data
                    logging.info(f"已加载 {len(self.photos_data)} 条已有的元数据。")
            except Exception as e:
                logging.error(f"加载元数据文件时出错: {e}")
                self.existing_md5 = set()
                self.photos_data = []
        else:
            self.existing_md5 = set()
            self.photos_data = []

    @staticmethod
    def calculate_md5(file_path):
        """计算文件的MD5值，用于检测文件是否已处理"""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logging.error(f"计算文件 {file_path} 的 MD5 时出错: {e}")
            return None

    @staticmethod
    def convert_to_degrees(value):
        """将GPS坐标转换为度的格式"""
        d, m, s = value
        return float(d) + (float(m) / 60.0) + (float(s) / 3600.0)

    @staticmethod
    def create_thumbnail(image, output_path, size=(200, 200)):
        """创建缩略图并保存到指定路径"""
        try:
            thumbnail = image.copy()
            thumbnail.thumbnail(size)
            thumbnail.save(output_path, "JPEG")
        except Exception as e:
            logging.error(f"创建缩略图 {output_path} 时出错: {e}")

    @staticmethod
    def convert_exif_to_serializable(exif_data):
        """递归地将EXIF数据中的非序列化类型转换为可序列化类型"""
        if isinstance(exif_data, dict):
            return {k: PhotoMetaExtractor.convert_exif_to_serializable(v) for k, v in exif_data.items()}
        elif isinstance(exif_data, tuple):
            return tuple(PhotoMetaExtractor.convert_exif_to_serializable(v) for v in exif_data)
        elif isinstance(exif_data, bytes):
            try:
                return exif_data.decode('utf-8', 'ignore')
            except Exception:
                return str(exif_data)
        else:
            return exif_data

    def extract_image_info(self, image_path, md5_hash):
        """提取图片的元数据信息，包括EXIF和GPS信息"""
        try:
            image = Image.open(image_path)
            exif_data = image._getexif()

            if not exif_data:
                return None

            exif = {}
            gps_info = {}

            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == "GPSInfo":
                    gps_info = {
                        GPSTAGS.get(key, key): value[key]
                        for key in value.keys()
                    }
                    exif[tag] = gps_info
                else:
                    exif[tag] = value

            # 提取GPS坐标信息
            if "GPSInfo" in exif:
                gps_info = exif["GPSInfo"]

                lat = self.convert_to_degrees(gps_info["GPSLatitude"])
                if gps_info.get("GPSLatitudeRef") in ["S", "南纬"]:
                    lat = -lat

                lon = self.convert_to_degrees(gps_info["GPSLongitude"])
                if gps_info.get("GPSLongitudeRef") in ["W", "西经"]:
                    lon = -lon

                coordinates = (lat, lon)
            else:
                return None  # 没有GPS信息，跳过

            # 生成缩略图
            filename = os.path.basename(image_path)
            thumbnail_path = os.path.join(self.thumbnail_dir, f"thumb_{filename}")
            self.create_thumbnail(image, thumbnail_path)

            # 将EXIF数据转换为可序列化的格式
            exif_serializable = self.convert_exif_to_serializable(exif)

            return {
                "filename": filename,
                "full_path": os.path.abspath(image_path),
                "coordinates": coordinates,
                "thumbnail": os.path.abspath(thumbnail_path),
                "original": os.path.abspath(image_path),
                "exif": exif_serializable,
                "md5": md5_hash
            }
        except Exception as e:
            logging.error(f"处理图片 {image_path} 时出错: {e}")
            return None

    def process_file(self, filename):
        """处理单个文件，提取元数据"""
        if any(filename.lower().endswith(f".{ext.lower()}") for ext in self.file_types):
            image_path = os.path.join(self.photo_dir, filename)
            md5_hash = self.calculate_md5(image_path)
            if not md5_hash:
                return None
            if md5_hash in self.existing_md5:
                logging.info(f"文件 {filename} 未改变，跳过处理。")
                return None
            photo_info = self.extract_image_info(image_path, md5_hash)
            if photo_info:
                return photo_info
        return None

    def process_photos(self):
        """处理目录中的所有照片，提取元数据"""
        logging.info("开始处理照片...")
        files = os.listdir(self.photo_dir)
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {executor.submit(self.process_file, filename): filename for filename in files}
            for future in tqdm(as_completed(futures), total=len(futures), desc="处理照片"):
                result = future.result()
                if result:
                    self.photos_data.append(result)
                    self.existing_md5.add(result['md5'])

    def persist_metadata(self):
        """将元数据保存为JSON文件"""
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.photos_data, f, ensure_ascii=False, indent=4)
            logging.info(f"元数据已保存到: {self.metadata_file}")
        except Exception as e:
            logging.error(f"保存元数据时出错: {e}")