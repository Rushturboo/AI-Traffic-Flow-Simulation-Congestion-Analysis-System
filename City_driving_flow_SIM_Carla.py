import carla
import random
import time
import math
import csv
import os
#.\CarlaUnreal.exe -quality-level=Low
#启动代码
try:
    import matplotlib.pyplot as plt
except ImportError:
    print("Matplotlib 未安装，画图功能需要它。请运行: pip install matplotlib")

def get_speed(vehicle):
    """计算车辆当前速度 (km/h)"""
    vel = vehicle.get_velocity()
    return 3.6 * math.sqrt(vel.x**2 + vel.y**2 + vel.z**2)

def get_congestion_level(avg_speed):
    """根据平均速度(km/h)判断拥堵等级"""
    if avg_speed > 25:  # 城市道路大于 25km/h 算通畅
        return "low", 1
    elif avg_speed > 10: # 10~25 km/h 算中等拥堵
        return "medium", 2
    else:               # 小于 10 km/h 算严重拥堵
        return "high", 3

def main():
    # 1. 连接 CARLA 客户端
    client = carla.Client('127.0.0.1', 2000)
    client.set_timeout(10.0)
    world = client.get_world()
    
    # 获取 Traffic Manager (TM) 用于控制自动驾驶
    traffic_manager = client.get_trafficmanager(8000)
    traffic_manager.set_global_distance_to_leading_vehicle(2.5)

    # 开启同步模式，确保数据采集稳定
    settings = world.get_settings()
    original_settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 0.05  # 20 FPS
    world.apply_settings(settings)
    traffic_manager.set_synchronous_mode(True)

    # 2. 准备生成 n 辆车
    blueprints = world.get_blueprint_library().filter('vehicle.*')
    # 过滤掉自行车和摩托车，避免奇怪的碰撞
    blueprints = [x for x in blueprints if int(x.get_attribute('number_of_wheels')) == 4]
    
    spawn_points = world.get_map().get_spawn_points()
    number_of_vehicles = 100 #改变车辆数
    
    if len(spawn_points) < number_of_vehicles:
        print(f"警告: 地图出生点({len(spawn_points)})少于请求车辆数({number_of_vehicles})")
        number_of_vehicles = len(spawn_points)
        
    # 随机打乱出生点
    random.shuffle(spawn_points)

    vehicles_list = []
    SpawnActor = carla.command.SpawnActor
    SetAutopilot = carla.command.SetAutopilot
    FutureActor = carla.command.FutureActor

    print(f"正在生成 {number_of_vehicles} 辆车...")
    batch = []
    for n, transform in enumerate(spawn_points[:number_of_vehicles]):
        blueprint = random.choice(blueprints)
        if blueprint.has_attribute('color'):
            color = random.choice(blueprint.get_attribute('color').recommended_values)
            blueprint.set_attribute('color', color)
        blueprint.set_attribute('role_name', 'autopilot')

        # 链式命令：生成车辆并立刻设为自动驾驶
        batch.append(SpawnActor(blueprint, transform)
                     .then(SetAutopilot(FutureActor, True, traffic_manager.get_port())))

    # 同步应用批量命令
    for response in client.apply_batch_sync(batch, True):
        if response.error:
            print(f"生成错误: {response.error}")
        else:
            vehicles_list.append(response.actor_id)

    print(f"成功生成并启动了 {len(vehicles_list)} 辆车的自动驾驶。")

    # 3. 准备数据记录
    csv_filename = "traffic_metrics.csv"
    with open(csv_filename, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['time', 'vehicle_count', 'avg_speed', 'congestion_level'])

    data_time = []
    data_density = []
    data_speed = []
    data_congestion_num = [] # 用数字记录拥堵等级画图(1=low, 2=medium, 3=high)
    data_positions = [] # 记录热力图位置信息

    start_time = world.get_snapshot().timestamp.elapsed_seconds
    MAX_SIMULATION_TIME = 60.0  # 设置最大仿真时间为 60 秒
    print(f"\n开始仿真并统计数据... (将自动运行 {MAX_SIMULATION_TIME} 秒后结束并生成图表)")

    try:
        while True:
            # 推进仿真帧
            world.tick()
            snapshot = world.get_snapshot()
            current_time = snapshot.timestamp.elapsed_seconds - start_time

            # 方案 B：达到指定时间自动停止
            if current_time >= MAX_SIMULATION_TIME:
                print(f"\n已达到预设的仿真时间 ({MAX_SIMULATION_TIME}s)，自动停止。")
                break

            # 获取所有当前存活的车辆
            actors = world.get_actors(vehicles_list)
            current_density = len(actors)
            
            if current_density == 0:
                print("所有车辆已消失，停止仿真。")
                break

            # 统计平均速度
            speeds = [get_speed(actor) for actor in actors]
            avg_speed = sum(speeds) / current_density
            
            # 判断拥堵等级
            congestion_level, congestion_num = get_congestion_level(avg_speed)

            # 收集位置用于热力图 (每10帧收集一次即可，避免数据量过大)
            if snapshot.frame % 10 == 0:
                for actor in actors:
                    loc = actor.get_location()
                    data_positions.append([loc.x, loc.y])

            # 记录数据
            data_time.append(current_time)
            data_density.append(current_density)
            data_speed.append(avg_speed)
            data_congestion_num.append(congestion_num)

            # 写入 CSV
            with open(csv_filename, mode='a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([f"{current_time:.2f}", current_density, f"{avg_speed:.2f}", congestion_level])

            # 控制台输出 (每秒钟的帧率大概是 20，这里用简单的条件降低打印频率)
            if snapshot.frame % 20 == 0:
                print(f"时间: {current_time:.1f}s | 车辆: {current_density} | 车速: {avg_speed:.2f} km/h | 状态: {congestion_level.upper()}")
            
            # 为了在 PyCharm 或其他终端中能更及时地响应中断，添加一点睡眠时间
            # 在 Windows 环境下，长时间被底层 C++ (Carla) 占用的死循环有时候无法捕获到 SIGINT。
            # 把 sleep 的时间稍微拉长一点点，给 Python 解释器留出更多响应时间。
            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n用户手动终止仿真。")
    finally:
        print(f"正在销毁 {len(vehicles_list)} 辆车...")
        client.apply_batch([carla.command.DestroyActor(x) for x in vehicles_list])
        
        # 恢复世界设置
        settings.synchronous_mode = False
        world.apply_settings(settings)

        # 4. 生成图表 (Density, Speed, Congestion, Heatmap)
        if 'plt' in globals() and len(data_time) > 0:
            print(f"正在生成图表并保存到 traffic_analysis.png ...")
            
            # 创建一个 2x2 的图表布局
            fig = plt.figure(figsize=(15, 10))

            # 图1：交通密度 vs 时间
            plt.subplot(2, 2, 1)
            plt.plot(data_time, data_density, 'b-', linewidth=2)
            plt.title('1. Traffic Density vs Time')
            plt.xlabel('Time (s)')
            plt.ylabel('Vehicle Count')
            plt.grid(True)

            # 图2：平均速度 vs 时间
            plt.subplot(2, 2, 2)
            plt.plot(data_time, data_speed, 'r-', linewidth=2)
            # 添加速度阈值的参考线
            plt.axhline(y=25, color='g', linestyle='--', alpha=0.5, label='Low Threshold (25)')
            plt.axhline(y=10, color='orange', linestyle='--', alpha=0.5, label='Medium Threshold (10)')
            plt.title('2. Average Speed vs Time')
            plt.xlabel('Time (s)')
            plt.ylabel('Average Speed (km/h)')
            plt.legend()
            plt.grid(True)

            # 图3：拥堵等级 vs 时间
            plt.subplot(2, 2, 3)
            # 1=low, 2=medium, 3=high
            plt.step(data_time, data_congestion_num, 'purple', where='post', linewidth=2)
            plt.yticks([1, 2, 3], ['Low', 'Medium', 'High'])
            plt.title('3. Congestion Level vs Time')
            plt.xlabel('Time (s)')
            plt.ylabel('Congestion Level')
            plt.grid(True)

            # 图4：交通密度热力图 (Heatmap)
            plt.subplot(2, 2, 4)
            if len(data_positions) > 0:
                x_vals = [p[0] for p in data_positions]
                y_vals = [p[1] for p in data_positions]
                # 用 hist2d 画热力图
                plt.hist2d(x_vals, y_vals, bins=50, cmap='YlOrRd')
                plt.colorbar(label='Density Frequency')
                plt.title('4. Traffic Density Heatmap (X-Y Locations)')
                plt.xlabel('Map X')
                plt.ylabel('Map Y')
            else:
                plt.title('4. Traffic Density Heatmap (No Data)')

            plt.tight_layout()
            plt.savefig('traffic_analysis.png', dpi=300)
            print(f"数据已保存至: {csv_filename}")
            print("图表生成完毕，正在显示...")
            # 在非阻塞模式下显示，然后等待用户关闭
            plt.show(block=True)
        else:
            print("未绘制图表 (没有数据或未安装 matplotlib)。")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
