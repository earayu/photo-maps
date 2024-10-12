import json
import logging
import os
from collections import defaultdict
from math import radians, sin, cos, sqrt, atan2

import folium
from folium.plugins import MarkerCluster, HeatMap


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

# metadata_file的example:
# [
#     {
#         "filename": "image_mock.JPG",
#         "full_path": "/mock/path/to/image_mock.JPG",
#         "coordinates": [
#             37.7749,
#             -122.4194
#         ],
#         "thumbnail": "/mock/path/to/thumbnails/thumb_image_mock.JPG",
#         "original": "/mock/path/to/image_mock.JPG",
#         "exif": {
#             "TileWidth": 512,
#             "TileLength": 512,
#             "GPSInfo": {
#                 "GPSLatitudeRef": "N",
#                 "GPSLatitude": [
#                     "37.0",
#                     "46.0",
#                     "29.64"
#                 ],
#                 "GPSLongitudeRef": "W",
#                 "GPSLongitude": [
#                     "122.0",
#                     "25.0",
#                     "9.84"
#                 ],
#                 "GPSAltitudeRef": "\u0000",
#                 "GPSAltitude": "30.0",
#                 "GPSSpeedRef": "K",
#                 "GPSSpeed": "0.0",
#                 "GPSImgDirectionRef": "T",
#                 "GPSImgDirection": "180.0",
#                 "GPSDestBearingRef": "T",
#                 "GPSDestBearing": "180.0",
#                 "GPSHPositioningError": "5.0"
#             },
#             "ResolutionUnit": 2,
#             "ExifOffset": 260,
#             "Make": "MockBrand",
#             "Model": "MockModel X",
#             "Software": "MockOS 1.0",
#             "Orientation": 1,
#             "DateTime": "2023:01:01 12:00:00",
#             "YCbCrPositioning": 1,
#             "XResolution": "72.0",
#             "YResolution": "72.0",
#             "HostComputer": "MockHost",
#             "ExifVersion": "0221",
#             "ComponentsConfiguration": "\u0001\u0002\u0003\u0000",
#             "ShutterSpeedValue": "8.0",
#             "DateTimeOriginal": "2023:01:01 12:00:00",
#             "DateTimeDigitized": "2023:01:01 12:00:00",
#             "ApertureValue": "2.0",
#             "BrightnessValue": "7.0",
#             "ExposureBiasValue": "0.0",
#             "MeteringMode": 5,
#             "Flash": 16,
#             "FocalLength": "35.0",
#             "ColorSpace": 1,
#             "ExifImageWidth": 6000,
#             "FocalLengthIn35mmFilm": 35,
#             "SceneCaptureType": 0,
#             "OffsetTime": "+00:00",
#             "OffsetTimeOriginal": "+00:00",
#             "OffsetTimeDigitized": "+00:00",
#             "SubsecTimeOriginal": "500",
#             "SubjectLocation": [
#                 2000,
#                 1500,
#                 400,
#                 300
#             ],
#             "SubsecTimeDigitized": "500",
#             "ExifImageHeight": 4000,
#             "SensingMethod": 2,
#             "ExposureTime": "0.001",
#             "FNumber": "2.0",
#             "SceneType": "\u0001",
#             "ExposureProgram": 2,
#             "ISOSpeedRatings": 100,
#             "ExposureMode": 0,
#             "FlashPixVersion": "0100",
#             "WhiteBalance": 0,
#             "LensSpecification": [
#                 "35.0",
#                 "85.0",
#                 "2.0",
#                 "4.0"
#             ],
#             "LensMake": "MockLensBrand",
#             "LensModel": "MockLensModel 35-85mm",
#             "CompositeImage": 2,
#             "MakerNote": "MockMakerNoteData"
#         },
#         "md5": "1234567890abcdef1234567890abcdef"
#     }
# ]