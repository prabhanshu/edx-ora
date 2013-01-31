from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

import json
import logging
import requests
import urlparse

import util
import grader_util

from staff_grading import staff_grading_util
from peer_grading import peer_grading_util

from models import Submission

from statsd import statsd

from django.db import connection

log = logging.getLogger(__name__)

_INTERFACE_VERSION=1


@csrf_exempt
def log_in(request):
    """
    Handles external login request.
    """
    if request.method == 'POST':
        p = request.POST.copy()
        if p.has_key('username') and p.has_key('password'):
            log.debug("Username: {0} Password: {1}".format(p['username'], p['password']))
            user = authenticate(username=p['username'], password=p['password'])
            if user is not None:
                login(request, user)
                log.debug("Successful login!")
                return util._success_response({'message' : 'logged in'} , _INTERFACE_VERSION)
            else:
                return util._error_response('Incorrect login credentials', _INTERFACE_VERSION)
        else:
            return util._error_response('Insufficient login info', _INTERFACE_VERSION)
    else:
        return util._error_response('login_required', _INTERFACE_VERSION)


def log_out(request):
    """
    Uses django auth to handle a logout request
    """
    logout(request)
    return util._success_response({'message' : 'Goodbye'} , _INTERFACE_VERSION)


def status(request):
    """
    Returns a simple status update
    """
    return util._success_response({'content' : 'OK'}, _INTERFACE_VERSION)

@csrf_exempt
@util.error_if_not_logged_in
def request_eta_for_submission(request):
    """
    Gets the ETA (in seconds) for a student submission to be graded for a given location
    Input:
        A problem location
    Output:
        Dictionary containing success, and eta indicating time a new submission for given location will take to be graded
    """
    if request.method != 'GET':
        return util._error_response("Request type must be GET", _INTERFACE_VERSION)

    location=request.GET.get("location")
    if not location:
        return util._error_response("Missing required key location", _INTERFACE_VERSION)

    success, eta = grader_util.get_eta_for_submission(location)

    if not success:
        return util._error_response(eta,_INTERFACE_VERSION)

    return util._success_response({
        'eta' : eta,
    }, _INTERFACE_VERSION)

@csrf_exempt
@login_required
def verify_name_uniqueness(request):
    """
    Check if a given problem name, location tuple is unique
    Input:
        A problem location and the problem name
    Output:
        Dictionary containing success, and and indicator of whether or not the name is unique
    """
    if request.method != 'GET':
        return util._error_response("Request type must be GET", _INTERFACE_VERSION)

    for tag in ['location', 'problem_name', 'course_id']:
        if tag not in request.GET:
            return util._error_response("Missing required key {0}".format(tag), _INTERFACE_VERSION)

    location=request.GET.get("location")
    problem_name = request.GET.get("problem_name")
    course_id = request.GET.get('course_id')

    success, unique = grader_util.check_name_uniqueness(problem_name,location, course_id)

    if not success:
        return util._error_response(eta,_INTERFACE_VERSION)

    return util._success_response({
        'name_is_unique' : unique,
        }, _INTERFACE_VERSION)

@csrf_exempt
@statsd.timed('open_ended_assessment.grading_controller.controller.views.time',
    tags=['function:check_for_notifications'])
@util.error_if_not_logged_in
def check_for_notifications(request):
    """
    Check if a given problem name, location tuple is unique
    Input:
        A problem location and the problem name
    Output:
        Dictionary containing success, and and indicator of whether or not the name is unique
    """
    if request.method != 'GET':
        return util._error_response("Request type must be GET", _INTERFACE_VERSION)

    for tag in ['course_id', 'user_is_staff', 'last_time_viewed', 'student_id']:
        if tag not in request.GET:
            return util._error_response("Missing required key {0}".format(tag), _INTERFACE_VERSION)

    request_dict = request.GET.copy()
    success, combined_notifications = grader_util.check_for_combined_notifications(request_dict)

    if not success:
        return util._error_response(combined_notifications,_INTERFACE_VERSION)

    util.log_connection_data()
    return util._success_response(combined_notifications, _INTERFACE_VERSION)

@csrf_exempt
@statsd.timed('open_ended_assessment.grading_controller.controller.views.time',
    tags=['function:get_grading_status_list'])
@util.error_if_not_logged_in
def get_grading_status_list(request):
    """
    Get a list of locations where student has submitted open ended questions and the status of each.
    Input:
        Course id, student id
    Output:
        Dictionary containing success, and a list of problems in the course with student submission status.
        See grader_util for format details.
    """
    if request.method != 'GET':
        return util._error_response("Request type must be GET", _INTERFACE_VERSION)

    for tag in ['course_id', 'student_id']:
        if tag not in request.GET:
            return util._error_response("Missing required key {0}".format(tag), _INTERFACE_VERSION)

    course_id = request.GET.get('course_id')
    student_id = request.GET.get('student_id')

    success, sub_list = grader_util.get_problems_student_has_tried(student_id, course_id)

    if not success:
        return util._error_response("Could not generate a submission list. {0}".format(sub_list),_INTERFACE_VERSION)

    problem_list_dict={
        'success' : success,
        'problem_list' : sub_list,
        }

    util.log_connection_data()
    return util._success_response(problem_list_dict, _INTERFACE_VERSION)

@csrf_exempt
@statsd.timed('open_ended_assessment.grading_controller.controller.views.time',
    tags=['function:get_flagged_problem_list'])
@util.error_if_not_logged_in
def get_flagged_problem_list(request):
    if request.method != 'GET':
        return util._error_response("Request type must be GET", _INTERFACE_VERSION)

    for tag in ['course_id']:
        if tag not in request.GET:
            return util._error_response("Missing required key {0}".format(tag), _INTERFACE_VERSION)

    success, flagged_submissions = peer_grading_util.get_flagged_submissions(request.GET.get('course_id'))

    if not success:
        return util._error_response("Could not generate a submission list. {0}".format(sub_list),_INTERFACE_VERSION)

    flagged_submission_dict={
        'success' : success,
        'flagged_submissions' : flagged_submissions,
        }

    util.log_connection_data()
    return util._success_response(flagged_submission_dict, _INTERFACE_VERSION)

@csrf_exempt
@statsd.timed('open_ended_assessment.grading_controller.controller.views.time',
    tags=['function:ban_student'])
@util.error_if_not_logged_in
def take_action_on_flags(request):
    if request.method != 'POST':
        return util._error_response("Request type must be POST", _INTERFACE_VERSION)

    for tag in ['course_id', 'student_id', 'action_type']:
        if tag not in request.GET:
            return util._error_response("Missing required key {0}".format(tag), _INTERFACE_VERSION)











