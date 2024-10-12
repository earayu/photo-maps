import os
import hashlib
import logging
from PIL import Image, ExifTags
from PIL.ExifTags import TAGS, GPSTAGS
import folium
from folium.plugins import MarkerCluster, HeatMap
import json
import toml
from tqdm import tqdm
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2
from concurrent.futures import ThreadPoolExecutor, as_completed

# 如果处理视频，需要安装 moviepy
# pip install moviepy
try:
    from moviepy.editor import VideoFileClip
except ImportError:
    VideoFileClip = None

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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


class PhotoMetaExtractor:
    def __init__(self, config):
        self.photo_dir = config.get('source_directory')
        self.output_dir = config.get('output_directory', "photo_data")
        self.file_types = config.get('file_types', ['jpg', 'jpeg', 'png'])
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
        if os.path.exists(self.metadata_file):
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
                self.existing_md5 = {item['md5'] for item in existing_data}
                self.photos_data = existing_data
                logging.info(f"已加载 {len(self.photos_data)} 条已有的元数据。")
        else:
            self.existing_md5 = set()
            self.photos_data = []

    def _calculate_md5(self, file_path):
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logging.error(f"计算文件 {file_path} 的 MD5 时出错: {e}")
            return None

    def _convert_to_degrees(self, value):
        d = float(value[0])
        m = float(value[1])
        s = float(value[2])
        return d + (m / 60.0) + (s / 3600.0)

    def _create_thumbnail(self, image, output_path, size=(200, 200)):
        try:
            thumbnail = image.copy()
            thumbnail.thumbnail(size)
            thumbnail.save(output_path, "JPEG")
        except Exception as e:
            logging.error(f"创建缩略图 {output_path} 时出错: {e}")

    def _convert_exif_to_serializable(self, exif_data):
        """递归地将 EXIF 数据中的非序列化类型转换为可序列化类型"""
        result = {}
        for key, value in exif_data.items():
            if isinstance(value, bytes):
                try:
                    value = value.decode('utf-8', 'ignore')
                except Exception:
                    value = str(value)
            elif isinstance(value, (int, float, str)):
                pass  # 基本类型，无需处理
            elif isinstance(value, tuple):
                value = tuple(
                    self._convert_exif_to_serializable(
                        {'': v}).get('') for v in value)
            elif isinstance(value, dict):
                value = self._convert_exif_to_serializable(value)
            else:
                # 尝试将其他类型转换为字符串
                value = str(value)
            result[key] = value
        return result

    def get_image_info(self, image_path, md5_hash):
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
                    for key in value.keys():
                        name = GPSTAGS.get(key, key)
                        gps_info[name] = value[key]
                    exif[tag] = gps_info
                else:
                    exif[tag] = value

            # 提取坐标信息
            if "GPSInfo" in exif:
                gps_info = exif["GPSInfo"]

                lat = self._convert_to_degrees(gps_info["GPSLatitude"])
                if gps_info["GPSLatitudeRef"] in ["S", "南纬"]:
                    lat = -lat

                lon = self._convert_to_degrees(gps_info["GPSLongitude"])
                if gps_info["GPSLongitudeRef"] in ["W", "西经"]:
                    lon = -lon

                coordinates = (lat, lon)
            else:
                return None  # 没有 GPS 信息

            # 生成缩略图
            filename = os.path.basename(image_path)
            thumbnail_path = os.path.join(self.thumbnail_dir, f"thumb_{filename}")
            self._create_thumbnail(image, thumbnail_path)

            # 将 EXIF 数据转换为可序列化的格式
            exif_serializable = self._convert_exif_to_serializable(exif)

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
        if any(filename.lower().endswith(f".{ext.lower()}") for ext in self.file_types):
            image_path = os.path.join(self.photo_dir, filename)
            md5_hash = self._calculate_md5(image_path)
            if not md5_hash:
                return None
            if md5_hash in self.existing_md5:
                logging.info(f"文件 {filename} 未改变，跳过处理。")
                return None
            photo_info = self.get_image_info(image_path, md5_hash)
            if photo_info:
                return photo_info
        return None

    def process_photos(self):
        logging.info("开始处理照片...")
        files = os.listdir(self.photo_dir)
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(self.process_file, filename): filename for filename in files}
            for future in tqdm(as_completed(futures), total=len(futures), desc="处理照片"):
                result = future.result()
                if result:
                    self.photos_data.append(result)
                    self.existing_md5.add(result['md5'])

    def persist_metadata(self):
        # 将元数据保存为 JSON 文件
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.photos_data, f, ensure_ascii=False, indent=4)
            logging.info(f"元数据已保存到: {self.metadata_file}")
        except Exception as e:
            logging.error(f"保存元数据时出错: {e}")


class MapperPlotter:
    def __init__(self, metadata_file, output_dir="photo_map"):
        self.metadata_file = metadata_file
        self.output_dir = output_dir

        # 创建输出目录
        os.makedirs(self.output_dir, exist_ok=True)

        # 加载元数据
        self.photos_data = self.load_metadata()

    def load_metadata(self):
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                photos_data = json.load(f)
                return photos_data
        except Exception as e:
            logging.error(f"加载元数据时出错: {e}")
            return []

    def _create_popup_html(self, photos_in_location):
        """创建增强的弹窗HTML，不包含JavaScript"""
        photos_html = ""
        for i, photo in enumerate(photos_in_location):
            datetime_original = photo['exif'].get('DateTimeOriginal', '未知时间')
            photos_html += f"""
                <div class="photo-item" data-src="{os.path.relpath(photo['original'], self.output_dir)}" data-filename="{photo['filename']}">
                    <img src="{os.path.relpath(photo['thumbnail'], self.output_dir)}" class="photo-thumb">
                    <div class="photo-info">
                        <div class="photo-name">{photo['filename']}</div>
                        <div class="photo-date">{datetime_original}</div>
                    </div>
                </div>
            """

        return f"""
        <div class="popup-container">
            <style>
                .popup-container {{
                    max-height: 300px;
                    overflow-y: auto;
                    min-width: 200px;
                    padding: 10px;
                }}
                .photo-item {{
                    display: flex;
                    align-items: center;
                    padding: 5px;
                    border-bottom: 1px solid #eee;
                    cursor: pointer;
                    transition: background-color 0.2s;
                }}
                .photo-item:hover {{
                    background-color: #f5f5f5;
                }}
                .photo-thumb {{
                    width: 50px;
                    height: 50px;
                    object-fit: cover;
                    border-radius: 4px;
                    margin-right: 10px;
                }}
                .photo-info {{
                    flex-grow: 1;
                }}
                .photo-name {{
                    font-weight: bold;
                    font-size: 12px;
                }}
                .photo-date {{
                    font-size: 11px;
                    color: #666;
                }}
            </style>

            <div class="photos-list">
                {photos_html}
            </div>
        </div>
        """

    def _group_nearby_photos(self, max_distance=50):  # max_distance in meters
        """将相近位置的照片分组"""
        groups = defaultdict(list)
        processed = set()

        for i, photo1 in enumerate(self.photos_data):
            if i in processed:
                continue

            current_group = []
            current_group.append(photo1)
            processed.add(i)

            lat1, lon1 = photo1['coordinates']

            for j, photo2 in enumerate(self.photos_data):
                if j in processed:
                    continue

                lat2, lon2 = photo2['coordinates']

                # 计算距离
                R = 6371000  # 地球半径（米）
                dlat = radians(lat2 - lat1)
                dlon = radians(lon2 - lon1)
                a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
                c = 2 * atan2(sqrt(a), sqrt(1 - a))
                distance = R * c

                if distance <= max_distance:
                    current_group.append(photo2)
                    processed.add(j)

            groups[len(groups)] = current_group

        return groups

    def create_map(self):
        if not self.photos_data:
            logging.warning("没有可用的照片数据，无法创建地图。")
            return

        # 创建地图对象
        m = folium.Map(
            tiles='https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            attr='&copy; OpenStreetMap contributors',
            zoom_start=12
        )

        # 添加暗色主题（可选）
        folium.TileLayer(
            tiles='https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
            attr='&copy; OpenStreetMap contributors &copy; CARTO',
            name='暗色主题'
        ).add_to(m)

        # 对照片进行分组
        photo_groups = self._group_nearby_photos()

        # 创建标记聚类和热力图数据
        marker_cluster = MarkerCluster(name='照片位置')
        heat_data = []

        # 添加照片组标记
        for group in photo_groups.values():
            # 使用组中第一张照片的位置
            location = group[0]['coordinates']

            # 创建包含组内所有照片的弹窗
            popup = folium.Popup(self._create_popup_html(group), max_width=300)

            # 根据组内照片数量选择图标颜色
            color = 'red' if len(group) == 1 else 'blue'
            icon = folium.Icon(color=color, icon='camera', prefix='fa')

            # 添加标记
            folium.Marker(
                location=location,
                popup=popup,
                icon=icon
            ).add_to(marker_cluster)

            # 添加到热力图数据
            heat_data.append([location[0], location[1], len(group)])

        # 添加标记聚类到地图
        marker_cluster.add_to(m)

        # 添加热力图图层
        HeatMap(
            heat_data,
            name='热力图',
            min_opacity=0.3,
            radius=15,
            blur=10
        ).add_to(m)

        # 添加图层控制
        folium.LayerControl().add_to(m)

        # 调整地图视角到所有照片的范围
        if self.photos_data:
            coordinates = [photo['coordinates'] for photo in self.photos_data]
            m.fit_bounds(coordinates)

        # 添加自定义 JavaScript 到地图
        custom_js = f"""
        <div id="image-modal" style="display:none; position:fixed; z-index:1000; left:0; top:0; width:100%; height:100%; background-color: rgba(0,0,0,0.9);">
            <span style="position:absolute; top:15px; right:35px; color:#f1f1f1; font-size:40px; font-weight:bold; cursor:pointer;">&times;</span>
            <img id="modal-image" style="margin:auto; display:block; width:80%; max-width:700px; max-height:80%; object-fit:contain;">
            <div id="modal-caption" style="margin:auto; display:block; width:80%; max-width:700px; text-align:center; color:#ccc; padding:10px 0; height:150px;"></div>
        </div>

        <script>
        document.addEventListener('DOMContentLoaded', (event) => {{
            const modal = document.getElementById('image-modal');
            const modalImg = document.getElementById('modal-image');
            const captionText = document.getElementById('modal-caption');
            const closeBtn = modal.querySelector('span');

            document.addEventListener('click', function(e) {{
                if (e.target.closest('.photo-item')) {{
                    const item = e.target.closest('.photo-item');
                    modal.style.display = "block";
                    modalImg.src = item.getAttribute('data-src');
                    captionText.innerHTML = item.getAttribute('data-filename');
                }}
            }});

            closeBtn.onclick = function() {{
                modal.style.display = "none";
            }}

            window.onclick = function(event) {{
                if (event.target == modal) {{
                    modal.style.display = "none";
                }}
            }}

            document.addEventListener('keydown', function(event) {{
                if (event.key === "Escape") {{
                    modal.style.display = "none";
                }}
            }});
        }});
        </script>
        """

        m.get_root().html.add_child(folium.Element(custom_js))

        # 保存地图
        output_map = os.path.join(self.output_dir, "photo_map.html")
        try:
            m.save(output_map)
            logging.info(f"地图已保存到: {output_map}")
        except Exception as e:
            logging.error(f"保存地图时出错: {e}")


# 使用示例
if __name__ == "__main__":
    # 读取配置文件
    try:
        config = Config('config.toml').settings
    except Exception as e:
        logging.error(f"加载配置文件时出错: {e}")
        exit(1)

    # 创建 PhotoMetaExtractor 对象并处理照片
    extractor = PhotoMetaExtractor(config)
    extractor.process_photos()

    # 持久化元数据到文件
    extractor.persist_metadata()

    # 创建 MapperPlotter 对象并绘制地图
    plotter = MapperPlotter(extractor.metadata_file, output_dir=config.get('output_directory', 'photo_map'))
    plotter.create_map()