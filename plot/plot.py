import os
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import folium
from folium.plugins import MarkerCluster, HeatMap
import pandas as pd
from datetime import datetime
from tqdm import tqdm
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2


class PhotoMetaExtractor:
    def __init__(self, photo_dir, output_dir="photo_data"):
        self.photo_dir = photo_dir
        self.output_dir = output_dir
        self.photos_data = []
        self.thumbnail_dir = os.path.join(output_dir, "thumbnails")

        # 创建输出目录
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.thumbnail_dir, exist_ok=True)

        self.dataframe = None

    def _convert_to_degrees(self, value):
        d = float(value[0])
        m = float(value[1])
        s = float(value[2])
        return d + (m / 60.0) + (s / 3600.0)

    def _create_thumbnail(self, image, output_path, size=(200, 200)):
        thumbnail = image.copy()
        thumbnail.thumbnail(size)
        thumbnail.save(output_path, "JPEG")

    def get_image_info(self, image_path):
        try:
            image = Image.open(image_path)
            exif = image._getexif()

            if not exif:
                return None

            gps_info = {}
            datetime_original = None

            for tag, value in exif.items():
                tag_name = TAGS.get(tag, tag)
                if tag_name == "GPSInfo":
                    for gps_tag in value:
                        sub_tag = GPSTAGS.get(gps_tag, gps_tag)
                        gps_info[sub_tag] = value[gps_tag]
                elif tag_name == "DateTimeOriginal":
                    datetime_original = value

            if not gps_info:
                return None

            lat = self._convert_to_degrees(gps_info["GPSLatitude"])
            if gps_info["GPSLatitudeRef"] == "S":
                lat = -lat

            lon = self._convert_to_degrees(gps_info["GPSLongitude"])
            if gps_info["GPSLongitudeRef"] == "W":
                lon = -lon

            # 生成缩略图
            filename = os.path.basename(image_path)
            thumbnail_path = os.path.join(self.thumbnail_dir, f"thumb_{filename}")
            self._create_thumbnail(image, thumbnail_path)

            return {
                "filename": filename,
                "coordinates": (lat, lon),
                "thumbnail": os.path.relpath(thumbnail_path, self.output_dir),
                "original": os.path.relpath(image_path, self.output_dir),
                "datetime": datetime_original
            }
        except Exception as e:
            print(f"处理图片 {image_path} 时出错: {str(e)}")
            return None

    def process_photos(self):
        print("正在处理照片...")
        for filename in tqdm(os.listdir(self.photo_dir)):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                image_path = os.path.join(self.photo_dir, filename)
                photo_info = self.get_image_info(image_path)
                if photo_info:
                    self.photos_data.append(photo_info)

        # 将数据转换为 DataFrame
        self.dataframe = pd.DataFrame(self.photos_data)

    def persist_dataframe(self, file_path):
        if self.dataframe is not None:
            self.dataframe.to_csv(file_path, index=False)
            print(f"元数据已保存到: {file_path}")
        else:
            print("DataFrame 为空，请先运行 process_photos() 方法。")


class MapperPlotter:
    def __init__(self, dataframe, output_dir="photo_map"):
        self.dataframe = dataframe
        self.output_dir = output_dir
        self.photos_data = dataframe.to_dict('records')
        self.thumbnail_dir = os.path.join(output_dir, "thumbnails")

        # 创建输出目录
        os.makedirs(self.output_dir, exist_ok=True)

    def _create_popup_html(self, photos_in_location):
        """创建增强的弹窗HTML，不包含JavaScript"""
        photos_html = ""
        for i, photo in enumerate(photos_in_location):
            photos_html += f"""
                <div class="photo-item" data-src="{photo['original']}" data-filename="{photo['filename']}">
                    <img src="{photo['thumbnail']}" class="photo-thumb">
                    <div class="photo-info">
                        <div class="photo-name">{photo['filename']}</div>
                        <div class="photo-date">{photo['datetime'] if photo['datetime'] else '未知时间'}</div>
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

        for idx1, photo1 in self.dataframe.iterrows():
            if idx1 in processed:
                continue

            current_group = []
            current_group.append(photo1)
            processed.add(idx1)

            lat1, lon1 = photo1['coordinates']

            for idx2, photo2 in self.dataframe.iterrows():
                if idx2 in processed:
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
                    processed.add(idx2)

            groups[len(groups)] = current_group

        return groups

    def create_map(self):
        # 创建地图对象
        m = folium.Map(
            tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            attr='&copy; OpenStreetMap contributors &copy; CARTO',
            zoom_start=12
        )

        # 添加暗色主题
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
        if not self.dataframe.empty:
            coordinates = self.dataframe['coordinates'].tolist()
            m.fit_bounds(coordinates)

        # 添加自定义 JavaScript 到地图
        custom_js = """
        <div id="image-modal" style="display:none; position:fixed; z-index:1000; left:0; top:0; width:100%; height:100%; background-color: rgba(0,0,0,0.9);">
            <span style="position:absolute; top:15px; right:35px; color:#f1f1f1; font-size:40px; font-weight:bold; cursor:pointer;">&times;</span>
            <img id="modal-image" style="margin:auto; display:block; width:80%; max-width:700px; max-height:80%; object-fit:contain;">
            <div id="modal-caption" style="margin:auto; display:block; width:80%; max-width:700px; text-align:center; color:#ccc; padding:10px 0; height:150px;"></div>
        </div>

        <script>
        document.addEventListener('DOMContentLoaded', (event) => {
            const modal = document.getElementById('image-modal');
            const modalImg = document.getElementById('modal-image');
            const captionText = document.getElementById('modal-caption');
            const closeBtn = modal.querySelector('span');

            document.addEventListener('click', function(e) {
                if (e.target.closest('.photo-item')) {
                    const item = e.target.closest('.photo-item');
                    modal.style.display = "block";
                    modalImg.src = item.getAttribute('data-src');
                    captionText.innerHTML = item.getAttribute('data-filename');
                }
            });

            closeBtn.onclick = function() {
                modal.style.display = "none";
            }

            window.onclick = function(event) {
                if (event.target == modal) {
                    modal.style.display = "none";
                }
            }

            document.addEventListener('keydown', function(event) {
                if (event.key === "Escape") {
                    modal.style.display = "none";
                }
            });
        });
        </script>
        """

        m.get_root().html.add_child(folium.Element(custom_js))

        # 保存地图
        output_map = os.path.join(self.output_dir, "photo_map.html")
        m.save(output_map)
        print(f"地图已保存到: {output_map}")


# 使用示例
if __name__ == "__main__":
    photo_directory = "/Users/earayu/Desktop/vlog素材/测试"  # 使用您提供的实际路径

    # 创建 PhotoMetaExtractor 对象并处理照片
    extractor = PhotoMetaExtractor(photo_directory)
    extractor.process_photos()

    # 持久化 DataFrame 到文件
    metadata_file = os.path.join(extractor.output_dir, "photos_metadata.csv")
    extractor.persist_dataframe(metadata_file)

    # 创建 MapperPlotter 对象并绘制地图
    plotter = MapperPlotter(extractor.dataframe)
    plotter.create_map()