#!/usr/bin/env python3
# coding: utf-8
import rospy
import numpy as np
import matplotlib.pyplot as plt
from nav_msgs.msg import Path
from std_msgs.msg import Header
from prediction.msg import TrackedPoint, TrackedPointHMM, PredictedHMMPath, PredictedObjectPath, PredictedObjectPathList, TrackedObjectPose, TrackedObjectPoseList, PredictedHMM, ObjectFrenetPosition
from morai_msgs.msg import ObjectStatusList
from utils import *

class HMMPredictionPath:
    def __init__(self):
        rospy.init_node("hmm_prediction_path_node", anonymous=True)
        rospy.Subscriber("/Object_topic", ObjectStatusList, self.object_info_callback)
        rospy.Subscriber("/global_path", Path, self.global_path_callback)
        rospy.Subscriber("/Object_topic/hmm_prediction", PredictedHMM, self.prediction_info_callback)
        rospy.Subscriber("/Object_topic/frenet_position", ObjectFrenetPosition, self.object_frenet_info_callback)
        self.hmm_based_path = rospy.Publisher('/Object_topic/hmm_predicted_path_topic', PredictedHMMPath, queue_size=10)

        self.rate = rospy.Rate(30)
        self.is_prediction_received = False
        self.prediction_data = None
        self.is_object = False
        self.is_global_path = False
        self.is_frenet_data = False
        self.frenet_data = None

    def object_frenet_info_callback(self, msg):
        self.is_frenet_data = True
        self.frenet_data = msg

    def global_path_callback(self, msg):
        self.global_path_msg = msg
        self.is_global_path = True

    def object_info_callback(self, msg):
        rospy.loginfo("Received Object info Message")
        self.is_object= True
        self.object_data = msg

    def prediction_info_callback(self, msg):
        rospy.loginfo("Received prediction msg data")
        self.is_prediction_received = True
        self.prediction_data = msg

    def generate_polynomial_path(self, start, end, order):
        # start and end is a type of tuple (x, y, dy/dx)
        x0, y0, dy0 = start
        x1, y1, dy1 = end

        if order == 3:
            # 3차 다항식: y = ax^3 + bx^2 + cx + d
            A = np.array([
                [x0**3, x0**2, x0, 1],
                [x1**3, x1**2, x1, 1],
                [3*x0**2, 2*x0, 1, 0],
                [3*x1**2, 2*x1, 1, 0]
            ])
            b = np.array([y0, y1, dy0, dy1])

        elif order == 5:
            # 5차 다항식: y = ax^5 + bx^4 + cx^3 + dx^2 + ex + f
            A = np.array([
                [x0**5, x0**4, x0**3, x0**2, x0, 1],
                [x1**5, x1**4, x1**3, x1**2, x1, 1],
                [5*x0**4, 4*x0**3, 3*x0**2, 2*x0, 1, 0],
                [5*x1**4, 4*x1**3, 3*x1**2, 2*x1, 1, 0],
                [20*x0**3, 12*x0**2, 6*x0, 2, 0, 0],
                [20*x1**3, 12*x1**2, 6*x1, 2, 0, 0]
            ])
            b = np.array([y0, y1, dy0, dy1, 0, 0])  # 가속도 0으로 가정
        coefficients = np.linalg.solve(A, b)
        return coefficients

    def evaluate_polynomial(self, coefficients, x_range):
        """다항식 계수와 x의 범위를 받아 y의 값을 계산합니다. points 집합"""
        return np.polyval(coefficients[::-1], x_range) # 100개 점 반환, 100개 크기의 배열 반환

    def create_lane_change_path(self, current_s, current_d, current_v, predicted_state, lane_width=3.521):
        # 경로 생성 시뮬레이션 범위
        path=[]
        s_range = np.linspace(current_s, current_s + 100, num=100)  # 100m 앞까지 예측
        right_end_d = current_s + lane_width
        right_coeff = self.generate_polynomial_path((current_s, current_d, current_v), (current_s + 50, right_end_d, current_v))

        #Left Change
        left_end_d = current_d - lane_width
        left_coeff = self.generate_polynomial_path((current_s, current_d, current_v), (current_s + 50, left_end_d, current_v))

        #Lane Keeping
        keeping_coeff =self.generate_polynomial_path((current_s, current_d, current_v), (current_s + 50, current_d, current_v))


        right_path = self.evaluate_polynomial(right_coeff, s_range)
        left_path = self.evaluate_polynomial(left_coeff, s_range)
        keeping_path = self.evaluate_polynomial(keeping_coeff, s_range)
        path=[keeping_path, right_path, left_path] # 각 100개씩 점 포함.
        return s_range, path

    def publish_predicted_paths(self):
        while not rospy.is_shutdown():
            if self.is_prediction_received and self.is_frenet_data:
                path_list=PredictedHMMPath()
                path_list.header = Header(stamp=rospy.Time.now(), frame_id="map")
                path_list.unique_id = self.prediction_data.unique_id
                mapx = [pose.pose.position.x for pose in self.global_path_msg.poses]
                mapy = [pose.pose.position.y for pose in self.global_path_msg.poses]
                maps=[0]
                for i in range(1, len(mapx)):
                    maps.append(maps[-1] + get_dist(mapx[i-1], mapy[i-1], mapx[i], mapy[i]))
                for prediction in self.prediction_data.probability:
                    path_list.unique_id = self.prediction_data.unique_id
                    s_range, paths = self.create_lane_change_path(
                        self.frenet_data.s, self.frenet_data.d, self.frenet_data.speed
                    )
                    if not paths:
                        rospy.logwarn("No paths generated for prediction")
                        continue
                    for maneuver_index, path_d in enumerate(paths):
                        points_array = []
                        for s, d in zip(s_range, path_d):
                            x, y = get_cartesian(s, d, mapx, mapy, maps)
                            point = TrackedPointHMM(x=x, y=y)
                            points_array.append(point)
                        if maneuver_index == 0 :
                            path_list.lane_keeping_path = points_array
                        elif maneuver_index == 1:
                            path_list.right_change_path = points_array
                        elif maneuver_index ==2:
                            path_list.left_change_path = points_array
                        print("path_list message: ", path_list)
                        self.hmm_based_path.publish(path_list)


if __name__ == "__main__":
    try:
        paths = HMMPredictionPath()
        paths.publish_predicted_paths()
    except rospy.ROSInitException:
        pass