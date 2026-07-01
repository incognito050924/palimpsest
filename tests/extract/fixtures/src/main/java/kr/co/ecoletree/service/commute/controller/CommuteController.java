package kr.co.ecoletree.service.commute.controller;

import java.io.IOException;
import java.text.ParseException;
import java.util.Map;

import javax.servlet.http.HttpServletRequest;

import kr.co.ecoletree.common.util.ResultUtil;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseBody;

import kr.co.ecoletree.common.auth.Auth;
import kr.co.ecoletree.common.base.web.ETBaseController;
import kr.co.ecoletree.common.helper.ETSessionHelper;
import kr.co.ecoletree.service.commoncode.service.CommonCodeService;
import kr.co.ecoletree.service.commute.service.CommuteService;
import kr.co.ecoletree.service.menuhistory.mapper.MenuHistoryMapper;


@Controller
//@RequestMapping("/service")
public class CommuteController extends ETBaseController {

	@Autowired
	CommuteService service;
	
	@Autowired
	CommonCodeService commonCodeService;
	
	@Autowired
	MenuHistoryMapper menuMapper;
	
	@Value("${INIT_PW}")
	String passWord;

//	private Logger logger = LoggerFactory.getLogger(this.getClass());
	
	/**
	 * 출퇴근부 초기화면
	 */
	@Auth
	@RequestMapping("/commute/main")
	public String commute(HttpServletRequest req) {
		Map<String, Object> param = getParamToMap(req);
		String cd = ETSessionHelper.getUserCd();
		param.put("user_cd", cd);
		menuMapper.menuHistoryInsert(param);
		return ".main.workMgt.commute";
	}
	
	/**
	 * 상태별 
	 * @param req
	 * @return
	 */
	@RequestMapping(value = "/selectAttedanceCondition")
	public @ResponseBody  Map<String, Object> selectAttedanceCondition(@RequestBody Map<String, Object> param,HttpServletRequest req) throws IOException {
//		Map<String, Object> param = getParamToMap(req);	
		Map<String, Object> map =  service.selectAttedanceCondition(param);
		return map;
	}
	
	/**
	 * 출근하기
	 * @param param
	 * @return
	 */
	@RequestMapping(value = "/insertGotoWork")
	public @ResponseBody Map<String, Object> insertGotoWork( @RequestBody Map<String, Object> param, HttpServletRequest request) throws IOException {
		return service.insertGotoWork(param,request);
	}
	
	/**
	 * 퇴근하기
	 * @param param
	 * @return
	 */
	@RequestMapping(value = "/updateOffWork")
	public @ResponseBody Map<String, Object> updateOffWork( @RequestBody Map<String, Object> param) throws IOException {
		return service.updateOffWork(param);
	}
	
	/**
	 * 휴가등록
	 * @param param
	 * @return
	 */
	@RequestMapping(value = "/insertGetVacation")
	public @ResponseBody Map<String, Object> insertGetVacation( @RequestBody Map<String, Object> param) throws IOException {
		return service.insertGetVacation(param);
	}
	
	/**
	 * 사용휴가, 남은휴가 가져오기
	 * @param param
	 * @return
	 */
	@RequestMapping(value = "/getDayOffCondition")
	public @ResponseBody Map<String, Object> getDayOffCondition( @RequestBody Map<String, Object> param) throws IOException {
		return service.getDayOffCondition(param);
	}

	/**
	 * 당월 총 몇시간 일했는지
	 * @param param
	 * @return
	 */
	@RequestMapping(value = "/selectWorkTimeMonthHandler")
	public @ResponseBody Map<String, Object> selectWorkTimeMonthHandler( @RequestBody Map<String, Object> param) throws IOException, ParseException {
		return service.selectWorkTimeMonthHandler(param);
	}
	
	/**
	 * 코드값
	 * @param param
	 * @return
	 */
	@RequestMapping(value = "/selectCodeList")
	public @ResponseBody Map<String, Object> selectCodeList( @RequestBody Map<String, Object> param) throws IOException {
		return service.selectCodeList(param);
	}

	/**
	 * 출퇴근부 수정 권한 확인
	 * @return
	 */
	@Auth
	@RequestMapping("/selectCheckEditable")
	public @ResponseBody boolean checkEditable(@RequestBody Map<String, Object> param) {
		return service.checkEditable(param);
	}


	/**
	 * 출근 취소
	 * @param param
	 * @return
	 */
	@RequestMapping("/cancelAttendance")
	public @ResponseBody Map<String, Object> cancelAttendance(@RequestBody Map<String, Object> param) {
		return ResultUtil.getResultMap(service.cancelAttendance(param));
	}

	/**
	 * 유급휴가 취소
	 * @param param
	 * @return
	 */
	@RequestMapping("/cancelVacation")
	public @ResponseBody Map<String, Object> cancelVacation(@RequestBody Map<String, Object> param){
		return ResultUtil.getResultMap(service.cancelVacation(param));

	}
	
}
